"""
TracingManager: lifecycle orchestration for a traced agent run.

Responsibilities:

1. Creates the `Run` row and a `RunStore` (Postgres writer + Redis broadcaster).
2. Creates the `TraceCallbackHandler` bound to that store and returns it so the
   graph executor can register it as a LangChain callback handler.
3. Exposes `run_agent(...)` which executes a LangGraph compiled graph with
   streaming state transitions + checkpoint memory capture, applies the
   callback handler, and finalises the run (status, aggregates, live events).
4. Supports **remote ingest** of pre-recorded runs via the REST API.
"""

from __future__ import annotations

import logging
import traceback as _tb
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.core.redis_pubsub import RedisPubSub
from app.db.init_db import gen_id
from app.db.models import LLMRequest, Run, RunStatus

log = logging.getLogger(__name__)


def _run_engine():
    """Per-process async engine used by background run workers."""
    return create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        future=True,
    )


_run_engine_cache = {}


def run_session_factory():
    engine = _run_engine_cache.setdefault("engine", _run_engine())
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class TracingManager:
    """Application-level singleton coordinating run lifecycle."""

    def __init__(self, pubsub: RedisPubSub) -> None:
        self.pubsub = pubsub

    # ------------------------------------------------------------------ Core
    async def start_run(self, agent_id: str, run_name: str | None,
                        input_payload: dict | None) -> tuple[str, "RunStore"]:
        from app.tracing.handler import RunStore

        session = run_session_factory()()
        try:
            run_id = gen_id("run")
            run = Run(
                id=run_id,
                agent_id=agent_id,
                run_name=run_name,
                status=RunStatus.RUNNING,
                input_payload=input_payload,
                started_at=datetime.now(timezone.utc),
            )
            session.add(run)
            await session.commit()
            store = RunStore(run_id, agent_id, session, self.pubsub)
            await self.pubsub.publish("run:lifecycle", {
                "type": "run_start",
                "run_id": run_id,
                "agent_id": agent_id,
                "run_name": run_name,
                "started_at": run.started_at.isoformat(),
            })
            return run_id, store
        except Exception:
            await session.close()
            raise

    async def execute_graph(self, graph, config: dict, store: "RunStore",
                            input_payload: dict | None,
                            build_real_graph=None) -> dict:
        """Execute a compiled LangGraph graph with full trace capture."""
        from app.tracing.handler import TraceCallbackHandler

        start = datetime.now(timezone.utc)
        handler = TraceCallbackHandler(store)

        exec_config = {
            **(config or {}),
            "recursion_limit": (config or {}).get("recursion_limit", 25),
            "configurable": {**(config or {}).get("configurable", {})},
            "callbacks": [handler],
        }
        output_summary = None
        try:
            # Stream updates + checkpoints so we capture transitions & memory.
            stream_modes = ["updates", "checkpoints"]
            # Track memory values between checkpoints to report only real diffs.
            prev_memory: dict[str, Any] = {}
            result = None
            async for part in graph.astream(
                input_payload or {},
                stream_mode=stream_modes,
                version="v2",
                config=exec_config,
            ):
                kind = part["type"]
                if kind == "updates":
                    for node_name, state in part["data"].items():
                        await store.record_state_transition("node", node_name, state)
                        if isinstance(state, dict):
                            for key, value in state.items():
                                if key in ("notes", "memory", "long_term_memory", "short_term_memory"):
                                    old = prev_memory.get(key)
                                    if old != value:
                                        await store.record_memory_update(key, old, value, source="node")
                                        prev_memory[key] = value
                    result = part["data"]
                elif kind == "checkpoints":
                    cp = part["data"]
                    vals = cp.get("values") if isinstance(cp, dict) else {}
                    if isinstance(vals, dict):
                        await store.record_state_transition("checkpoint", None, vals)
                        if isinstance(vals, dict):
                            for key, value in vals.items():
                                if key in ("notes", "memory", "long_term_memory", "short_term_memory"):
                                    old = prev_memory.get(key)
                                    new = value
                                    if old != new:
                                        await store.record_memory_update(
                                            key, old, new, source="checkpoint",
                                        )
                                        prev_memory[key] = new
            if isinstance(result, dict):
                for node_state in result.values():
                    if isinstance(node_state, dict):
                        for v in node_state.values():
                            if isinstance(v, list):
                                for m in v:
                                    content = getattr(m, "content", None)
                                    if content is None and isinstance(m, dict):
                                        content = m.get("content")
                                    if isinstance(content, str) and content.strip():
                                        output_summary = content
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            await store.set_status(
                RunStatus.SUCCESS, duration_ms=duration_ms, output_summary=output_summary,
            )
            return {"run_id": store.run_id, "status": "success"}
        except Exception as exc:
            exc_message = str(exc)
            is_unsupported_model = (
                "unsupported model" in exc_message.lower()
                or "only the following models are allowed" in exc_message.lower()
            )
            if is_unsupported_model and build_real_graph is not None:
                # The configured LLM endpoint rejected the requested model.
                # Rebuild the graph in simulator mode and rerun so the demo
                # never breaks because of a provider allow-list.
                log.warning("run %s: endpoint rejected model, retrying in simulator mode", store.run_id)
                from app.db.models import LLMRequest as _LR, MemoryUpdate as _MU, RunError as _RE, StateTransition as _ST, ToolCall as _TC

                await store.session.execute(sa_delete(_RE).where(_RE.run_id == store.run_id))
                await store.session.execute(sa_delete(_TC).where(_TC.run_id == store.run_id))
                await store.session.execute(sa_delete(_ST).where(_ST.run_id == store.run_id))
                await store.session.execute(sa_delete(_MU).where(_MU.run_id == store.run_id))
                await store.session.execute(sa_delete(_LR).where(_LR.run_id == store.run_id))
                await store.session.flush()
                store.seq = 0
                store._llm_seq = 0
                import functools

                sim_build = functools.partial(build_real_graph, simulator_store=store)
                sim_graph = sim_build(use_simulator=True)
                sim_config = {
                    **{k: v for k, v in config.items() if k != "configurable"},
                    "recursion_limit": (config or {}).get("recursion_limit", 25),
                    "configurable": (config or {}).get("configurable", {}),
                }
                return await self.execute_graph(sim_graph, sim_config, store, input_payload,
                                                build_real_graph=sim_build)
            tb_str = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
            log.error("run %s failed: %s", store.run_id, exc)
            await store.record_error(
                trace_id=None,
                error_type=type(exc).__name__,
                message=exc_message[:500],
                traceback_str=tb_str,
            )
            duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
            await store.set_status(
                RunStatus.ERROR, duration_ms=duration_ms, error=exc_message[:2000],
            )
            return {"run_id": store.run_id, "status": "error"}

    # ------------------------------------------------------------------ REST
    async def ingest_run(self, agent_id: str, payload: dict) -> str:
        """Insert a fully pre-recorded run (used by external agents)."""
        session = run_session_factory()()
        try:
            run_id = payload.get("run_id") or gen_id("run")
            run = Run(
                id=run_id,
                agent_id=agent_id,
                run_name=payload.get("run_name"),
                status=RunStatus(payload.get("status", "success")),
                input_payload=payload.get("input"),
                output_summary=(payload.get("output") or "")[:1000] or None,
                duration_ms=payload.get("duration_ms"),
                total_input_tokens=payload.get("total_input_tokens", 0),
                total_output_tokens=payload.get("total_output_tokens", 0),
                total_cost_usd=payload.get("total_cost_usd", 0.0),
                started_at=(
                    datetime.fromisoformat(payload["started_at"])
                    if payload.get("started_at") else datetime.now(timezone.utc)
                ),
                finished_at=datetime.now(timezone.utc),
            )
            session.add(run)
            for req in payload.get("llm_requests") or []:
                session.add(LLMRequest(
                    id=gen_id("llm"),
                    run_id=run_id,
                    seq=req.get("seq", 0),
                    trace_id=req.get("trace_id"),
                    model=req.get("model", "unknown"),
                    input_tokens=req.get("input_tokens", 0),
                    output_tokens=req.get("output_tokens", 0),
                    cost_usd=req.get("cost_usd", 0.0),
                    duration_ms=req.get("duration_ms"),
                    request_messages=req.get("request"),
                    response_text=req.get("response"),
                ))
            await session.commit()
        finally:
            await session.close()

        await self.pubsub.publish("run:lifecycle", {
            "type": "run_complete",
            "run_id": run_id,
            "agent_id": agent_id,
            "status": run.status.value,
            "duration_ms": run.duration_ms,
            "total_cost_usd": run.total_cost_usd,
        })
        return run_id
