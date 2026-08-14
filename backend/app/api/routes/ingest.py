"""
Remote ingest API.

External LangGraph agents (running anywhere) can push pre-recorded run data
here in the same schema the platform natively produces, making the dashboard
a universal viewer for any agent that instruments itself.

Example (Python, using `httpx`):

    await client.post("http://host/api/ingest/runs", json={
        "agent_id": "my-agent",
        "run_name": "user-42 question",
        "status": "success",
        "duration_ms": 1250,
        "total_input_tokens": 420,
        "total_output_tokens": 180,
        "total_cost_usd": 0.00054,
        "llm_requests": [{"model": "gpt-4o-mini", "input_tokens": 400,
                          "output_tokens": 180, "duration_ms": 900}],
    })
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestRun, IngestResponse
from app.db.models import Agent
from app.db.session import get_db

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/runs", response_model=IngestResponse, status_code=201)
async def ingest_run(body: IngestRun, db: AsyncSession = Depends(get_db),
                     request: Request = None):
    agent = (await db.execute(select(Agent).where(Agent.id == body.agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(
            404, f"Agent {body.agent_id} not found — register it first via POST /api/agents",
        )
    manager = request.app.state.tracing_manager
    run_id = await manager.ingest_run(body.agent_id, body.model_dump())
    return IngestResponse(run_id=run_id, status=body.status)
