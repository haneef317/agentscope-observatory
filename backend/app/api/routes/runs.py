"""Run listing, detail and invoke endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import build_graph
from app.api.schemas import InvokeRequest, RunDetail, RunSummary
from app.core.config import settings
from app.db.models import Agent, LLMRequest, MemoryUpdate, Run, RunStatus, StateTransition, ToolCall, RunError
from app.db.session import get_db
from app.tracing.manager import TracingManager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])


def _to_dict(row) -> dict:
    d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


@router.get("", response_model=list[RunSummary])
async def list_runs(
    agent_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = select(Run)
    if agent_id:
        q = q.where(Run.agent_id == agent_id)
    if status:
        q = q.where(Run.status == RunStatus(status))
    if search:
        q = q.where(
            (Run.run_name.ilike(f"%{search}%"))
            | (Run.output_summary.ilike(f"%{search}%"))
            | (Run.id.ilike(f"%{search}%"))
        )
    result = await db.execute(q.order_by(desc(Run.created_at)).limit(limit).offset(offset))
    return result.scalars().all()


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")

    async def rows(model):
        q = (select(model).where(model.run_id == run_id).order_by(model.seq.asc())
             if hasattr(model, "seq") else
             select(model).where(model.run_id == run_id))
        result = await db.execute(q)
        return [_to_dict(r) for r in result.scalars().all()]

    return RunDetail(
        **_to_dict(run),
        llm_requests=await rows(LLMRequest),
        tool_calls=await rows(ToolCall),
        state_transitions=await rows(StateTransition),
        memory_updates=await rows(MemoryUpdate),
        errors=[_to_dict(e) for e in (await db.execute(select(RunError).where(RunError.run_id == run_id))).scalars().all()],
    )


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    await db.delete(run)
    await db.commit()


@router.post("/agents/{agent_id}/invoke")
async def invoke_agent(agent_id: str, body: InvokeRequest):
    """
    Execute a registered agent with full trace capture.

    Returns immediately with a run id; the frontend subscribes to
    `ws/runs/{run_id}` to receive live trace events.
    """
    from app.core.redis_pubsub import RedisPubSub
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        agent = await db.get(Agent, agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} not found")

        # Real LLM mode is used when an API key is configured AND the
        # requested model is one the platform supports natively. Everything
        # else (no key, or an unsupported/unknown model) runs in simulator
        # mode so demos never break because of provider allow-lists.
        use_simulator = not settings.real_llm_enabled
        if not use_simulator:
            from app.core.cost import DEFAULT_PRICING

            known = any(k in agent.model.lower() for k in DEFAULT_PRICING)
            if not known:
                log.warning(
                    "model %s has no pricing entry / is unsupported by the configured "
                    "LLM endpoint, running in simulator mode", agent.model,
                )
                use_simulator = True

        import functools

        sim_build = functools.partial(build_graph, simulator_store=None)
        graph = build_graph(model=agent.model or "", use_simulator=use_simulator)
        if use_simulator:
            # simulated graph expects {messages: [...], notes, steps_used}
            payload = {
                "messages": [{"type": "human", "content": body.message}],
                "notes": [],
                "steps_used": 0,
            }
        else:
            payload = {"messages": [{"type": "human", "content": body.message}]}

        pubsub = RedisPubSub(settings.REDIS_URL)
        await pubsub.connect()
        manager = TracingManager(pubsub)
        run_id, store = await manager.start_run(agent_id, body.run_name, payload)
        config = agent.config or {}
        thread_id = run_id  # checkpointer thread = run id
        config = {**config, "configurable": {"thread_id": thread_id}, "recursion_limit": config.get("recursion_limit", 25)}

        # Execute in the background so the HTTP response returns fast.
        import asyncio

        async def _run():
            log.info("_run task started for %s", run_id)
            try:
                # build_real_graph lets the manager rebuild the graph in
                # simulator mode if the configured LLM endpoint rejects it.
                sim_build = functools.partial(build_graph, simulator_store=store)
                await manager.execute_graph(
                    sim_build(model=agent.model or "", use_simulator=use_simulator),
                    config, store, payload,
                    build_real_graph=sim_build,
                )
                log.info("_run execute_graph done for %s", run_id)
                await store.session.commit()
                log.info("_run committed for %s", run_id)
            except Exception:
                log.exception("_run failed for %s", run_id)
                await store.session.rollback()
                raise
            finally:
                await store.session.close()
                log.info("_run finished for %s", run_id)

        asyncio.create_task(_run())
        return JSONResponse({"run_id": run_id, "agent_id": agent_id, "status": "running"}, status_code=202)
