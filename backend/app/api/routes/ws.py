"""
WebSocket endpoints for real-time trace streaming.

Endpoints
---------
GET /api/ws/runs/{run_id}       — live events for one run
GET /api/ws/lifecycle           — global stream of run start/complete events

Each connection subscribes to the corresponding Redis channel and forwards
every JSON event to the client until disconnect. Keep-alive pings are sent
every 20 seconds to traverse proxies that kill idle connections.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["websocket"])


async def _redis_bridge(ws: WebSocket, channels: list[str]) -> None:
    """Subscribe to Redis channels and pump messages into the WebSocket."""
    from app.core.redis_pubsub import RedisPubSub

    pubsub = RedisPubSub(settings.REDIS_URL)
    if not await pubsub.connect():
        # No Redis: send a helpful hint and close gracefully.
        await ws.send_json({"type": "system", "message": "live stream unavailable (Redis not connected)"})
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    for ch in channels:
        await pubsub.subscribe(ch, queue)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    break
                continue
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                await ws.send_json(payload)
            except Exception as exc:
                log.debug("ws send error: %s", exc)
                break
    finally:
        for ch in channels:
            await pubsub.unsubscribe(ch)


async def _replay_stored_events(ws: WebSocket, run_id: str) -> None:
    """Send events already persisted in the database so late-joining clients
    (for example the Playground after a run has finished) see the full trace.

    The payloads mirror the live event shapes emitted by RunStore so the
    frontend handles replay and live events identically.
    """
    from app.db.models import LLMRequest, MemoryUpdate, Run, RunError, StateTransition, ToolCall
    from app.db.session import engine
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(engine) as session:
        run = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
        if run is None:
            await ws.send_json({"type": "system", "message": "unknown run id"})
            return

        async def send_rows(query):
            rows = (await session.execute(query)).scalars().all()
            for r in rows:
                await ws.send_json(r.to_event())

        await send_rows(
            select(LLMRequest).where(LLMRequest.run_id == run_id).order_by(LLMRequest.seq),
        )
        await send_rows(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.finished_at.asc().nullsfirst()),
        )
        await send_rows(
            select(StateTransition).where(StateTransition.run_id == run_id).order_by(StateTransition.seq),
        )
        await send_rows(
            select(MemoryUpdate).where(MemoryUpdate.run_id == run_id).order_by(MemoryUpdate.seq),
        )
        await send_rows(
            select(RunError).where(RunError.run_id == run_id).order_by(RunError.occurred_at),
        )
        if run.status.value == "running":
            await ws.send_json({
                "type": "run_start",
                "run_id": run_id,
                "agent_id": run.agent_id,
                "run_name": run.run_name,
                "started_at": run.started_at.isoformat() if run.started_at else None,
            })
        else:
            await ws.send_json({
                "type": "run_complete",
                "run_id": run_id,
                "agent_id": run.agent_id,
                "status": run.status.value,
                "duration_ms": run.duration_ms,
                "total_input_tokens": run.total_input_tokens,
                "total_output_tokens": run.total_output_tokens,
                "total_cost_usd": run.total_cost_usd,
                "output_summary": run.output_summary,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            })


@router.websocket("/runs/{run_id}")
async def run_events(ws: WebSocket, run_id: str):
    await ws.accept()
    try:
        await _replay_stored_events(ws, run_id)
        await _redis_bridge(ws, [f"run:{run_id}"])
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("ws error run %s: %s", run_id, exc)


@router.websocket("/lifecycle")
async def lifecycle_events(ws: WebSocket):
    await ws.accept()
    try:
        await _redis_bridge(ws, ["run:lifecycle"])
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("ws lifecycle error: %s", exc)
