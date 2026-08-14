"""
Tracing instrumentation for LangGraph agents.

Two complementary mechanisms are combined:

1. **LangChain BaseCallbackHandler** (`TraceCallbackHandler`) — captures LLM
   spans (start/end_llm) with model, tokens, latency and the raw messages, as
   well as tool spans (on_tool_start/on_tool_end) with inputs/outputs/errors.

2. **LangGraph stream modes** (see `agents/graph.py`) — captures state
   transitions, checkpoints (memory writes) and task errors during graph
   execution.

Both write to the same `RunStore` which persists to PostgreSQL and publishes
live events to Redis so WebSocket subscribers see traces in real time.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage

from app.core.cost import estimate_cost, model_provider
from app.core.redis_pubsub import RedisPubSub
from app.db.init_db import gen_id
from app.db.models import LLMRequest, MemoryUpdate, RunError, RunStatus, StateTransition, ToolCall

log = logging.getLogger(__name__)


def _msg_to_dict(msg: BaseMessage | dict) -> dict:
    """Normalize a BaseMessage into a JSON-serializable dict."""
    if isinstance(msg, dict):
        return msg
    return {
        "type": msg.type,
        "role": getattr(msg, "name", None) or msg.type,
        "content": msg.content if isinstance(msg.content, str) else str(msg.content),
        "tool_calls": [
            {"name": tc.get("name"), "arguments": tc.get("args")}
            for tc in (msg.tool_calls or [])
        ] if isinstance(msg, AIMessage) else None,
        "tool_call_id": getattr(msg, "tool_call_id", None),
    }


def _serialize(obj: Any) -> Any:
    """Best-effort JSON serialization of arbitrary python objects."""
    if isinstance(obj, BaseMessage):
        return _msg_to_dict(obj)
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    try:
        import json

        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


class RunStore:
    """
    Write-ahead store: persists spans to PostgreSQL and fans out live events
    over Redis pub/sub. One instance per agent run; created by TracingManager.
    """

    def __init__(self, run_id: str, agent_id: str, session, pubsub: RedisPubSub) -> None:
        self.run_id = run_id
        self.agent_id = agent_id
        self.session = session          # dedicated long-lived async session
        self.pubsub = pubsub
        self.seq = 0
        self._llm_seq = 0
        self._llm_trace_ids: list[str] = []

    # --------------------------------------------------------------------
    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    async def _publish(self, event: dict) -> None:
        await self.pubsub.publish(f"run:{self.run_id}", event)
        await self.pubsub.publish("run:lifecycle", event)

    # ---------------------------------------------------------------- LLM
    def record_llm_start(self, *, model: str, messages: list, params: dict | None = None) -> str:
        trace_id = uuid.uuid4().hex[:16]
        self._llm_seq += 1
        req = LLMRequest(
            id=gen_id("llm"),
            run_id=self.run_id,
            seq=self._llm_seq,
            trace_id=trace_id,
            model=model,
            provider=model_provider(model),
            started_at=datetime.now(timezone.utc),
            request_messages=_serialize(messages),
            invocation_params=_serialize(params or {}),
        )
        self.session.add(req)
        self._llm_trace_ids.append(trace_id)
        return trace_id

    async def finish_llm(self, trace_id: str, *, input_tokens: int, output_tokens: int,
                         finish_reason: str | None, response_text: str | None,
                         duration_ms: float) -> None:
        from sqlalchemy import select

        req = (await self.session.execute(
            select(LLMRequest).where(LLMRequest.trace_id == trace_id))).scalar_one_or_none()
        if req is None:
            return
        req.input_tokens = input_tokens
        req.output_tokens = output_tokens
        req.finish_reason = finish_reason
        req.response_text = response_text[:4000] if response_text else None
        req.duration_ms = int(duration_ms)
        req.finished_at = datetime.now(timezone.utc)
        req.cost_usd = estimate_cost(req.model, input_tokens, output_tokens)
        await self.session.flush()

        await self._publish({
            "type": "llm_span",
            "run_id": self.run_id,
            "span_id": req.id,
            "trace_id": trace_id,
            "model": req.model,
            "provider": req.provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": req.cost_usd,
            "duration_ms": req.duration_ms,
            "finish_reason": finish_reason,
            "response_text": req.response_text,
            "started_at": req.started_at.isoformat(),
            "finished_at": req.finished_at.isoformat(),
        })

    # ------------------------------------------------------------------ Tool
    async def record_tool_start(self, tool_name: str, tool_input: dict) -> str:
        trace_id = uuid.uuid4().hex[:16]
        call = ToolCall(
            id=gen_id("tool"),
            run_id=self.run_id,
            trace_id=trace_id,
            tool_name=tool_name,
            input_data=_serialize(tool_input),
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(call)
        await self.session.flush()
        await self._publish({
            "type": "tool_start",
            "run_id": self.run_id,
            "span_id": call.id,
            "trace_id": trace_id,
            "tool_name": tool_name,
            "input_data": call.input_data,
            "started_at": call.started_at.isoformat(),
        })
        return trace_id

    async def finish_tool(self, trace_id: str, *, output: Any, success: bool,
                          error: str | None, duration_ms: float) -> None:
        from sqlalchemy import select

        call = (await self.session.execute(
            select(ToolCall).where(ToolCall.trace_id == trace_id))).scalar_one_or_none()
        if call is None:
            return
        call.output_data = _serialize(output)
        call.success = success
        call.error = error
        call.duration_ms = int(duration_ms)
        call.finished_at = datetime.now(timezone.utc)
        await self.session.flush()

        await self._publish({
            "type": "tool_end",
            "run_id": self.run_id,
            "span_id": call.id,
            "trace_id": trace_id,
            "tool_name": call.tool_name,
            "output_data": call.output_data,
            "success": success,
            "error": error,
            "duration_ms": call.duration_ms,
            "started_at": call.started_at.isoformat(),
            "finished_at": call.finished_at.isoformat(),
        })

    # ------------------------------------------------------------- State
    async def record_state_transition(self, step_type: str, node_name: str | None,
                                      state: dict | None) -> None:
        seq = self._next_seq()
        tr = StateTransition(
            id=gen_id("st"),
            run_id=self.run_id,
            seq=seq,
            step_type=step_type,
            node_name=node_name,
            state_snapshot=_serialize(state) if state else {},
        )
        self.session.add(tr)
        await self.session.flush()
        await self._publish({
            "type": "state_transition",
            "run_id": self.run_id,
            "seq": seq,
            "step_type": step_type,
            "node_name": node_name,
            "state_snapshot": _serialize(state) if state else {},
        })

    # ------------------------------------------------------------- Memory
    async def record_memory_update(self, memory_key: str, old_value: Any,
                                   new_value: Any, source: str = "checkpoint") -> None:
        seq = self._next_seq()
        mu = MemoryUpdate(
            id=gen_id("mem"),
            run_id=self.run_id,
            seq=seq,
            source=source,
            memory_key=memory_key,
            old_value=_serialize(old_value),
            new_value=_serialize(new_value),
        )
        self.session.add(mu)
        await self.session.flush()
        await self._publish({
            "type": "memory_update",
            "run_id": self.run_id,
            "seq": seq,
            "source": source,
            "memory_key": memory_key,
            "old_value": _serialize(old_value),
            "new_value": _serialize(new_value),
        })

    # -------------------------------------------------------------- Error
    async def record_error(self, trace_id: str | None, error_type: str,
                           message: str, traceback_str: str | None) -> None:
        err = RunError(
            id=gen_id("err"),
            run_id=self.run_id,
            trace_id=trace_id,
            error_type=error_type,
            message=message[:2000] if message else None,
            traceback=traceback_str[:8000] if traceback_str else None,
        )
        self.session.add(err)
        await self.session.flush()
        await self._publish({
            "type": "error",
            "run_id": self.run_id,
            "trace_id": trace_id,
            "error_type": error_type,
            "message": message,
            "traceback": traceback_str,
        })

    # --------------------------------------------------------- Lifecycle
    async def set_status(self, status: RunStatus, *, duration_ms: int | None = None,
                         total_input_tokens: int = 0, total_output_tokens: int = 0,
                         total_cost: float = 0.0, output_summary: str | None = None,
                         error: str | None = None) -> None:
        from sqlalchemy import select, func

        from app.db.models import Run

        run = (await self.session.execute(
            select(Run).where(Run.id == self.run_id))).scalar_one_or_none()
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        if duration_ms is not None:
            run.duration_ms = duration_ms
        agg = await self.session.execute(
            select(
                func.sum(LLMRequest.input_tokens).label("tok_in"),
                func.sum(LLMRequest.output_tokens).label("tok_out"),
                func.sum(LLMRequest.cost_usd).label("cost"),
            ).where(LLMRequest.run_id == self.run_id)
        )
        row = agg.one()
        run.total_input_tokens = int(row.tok_in or 0)
        run.total_output_tokens = int(row.tok_out or 0)
        run.total_cost_usd = float(row.cost or 0.0)
        if output_summary:
            run.output_summary = output_summary[:1000]
        if error:
            run.error = error[:2000]
        await self.session.flush()
        await self._publish({
            "type": "run_complete",
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "status": status.value,
            "duration_ms": run.duration_ms,
            "total_input_tokens": run.total_input_tokens,
            "total_output_tokens": run.total_output_tokens,
            "total_cost_usd": run.total_cost_usd,
            "finished_at": run.finished_at.isoformat(),
        })


class TraceCallbackHandler(BaseCallbackHandler):
    """
    LangChain callback handler that captures LLM and tool spans.

    - start_llm/end_llm  → LLMRequest rows (model, tokens, cost, latency, messages)
    - on_tool_start/end  → ToolCall rows (input, output, success, latency)
    """

    def __init__(self, store: RunStore) -> None:
        super().__init__()
        self.store = store
        self._llm_start: dict[str, tuple[str, float, list]] = {}   # run_id -> (model, t0, messages)
        self._tool_start: dict[str, tuple[str, float]] = {}        # trace_id -> (name, t0)

    # ------------------------------------------------------------------ LLM
    def start_llm(self, model: str, messages: list, *args: Any, **kwargs: Any) -> None:
        # LangChain>=1 core invokes end_llm with a dict payload; we store a
        # placeholder start marker keyed by the model name.
        self._llm_start.setdefault(model, (model, time.time(), messages))

    def end_llm(self, model: str, messages: list, *args: Any, **kwargs: Any) -> None:
        pass  # token usage arrives via end_llm payload in newer core; handled below

    async def on_llm_start(self, serialized: dict, prompts: list[str], *,
                           run_id, parent_run_id, **kwargs: Any) -> None:
        try:
            params = kwargs.get("invocation_params") or {}
            model = params.get("model") or serialized.get("name", "unknown")
            # Collect messages if provided by newer LangChain core
            messages = kwargs.get("messages") or [{"type": "user", "content": p} for p in prompts]
            store = self.store
            trace_id = store.record_llm_start(model=model, messages=messages, params=params)
            self._llm_start[str(run_id)] = (trace_id, time.time(), model)
        except Exception:
            log.exception("trace: llm start failed")

    async def on_llm_end(self, response, *, run_id, parent_run_id, **kwargs: Any) -> None:
        try:
            marker = self._llm_start.pop(str(run_id), None)
            if not marker:
                return
            trace_id, t0, model = marker
            duration_ms = (time.time() - t0) * 1000

            input_tokens = output_tokens = 0
            finish_reason = None
            response_text = None
            try:
                generations = getattr(response, "generations", []) or []
                for gen_list in generations:
                    for gen in gen_list:
                        text = getattr(gen, "text", None)
                        if text:
                            response_text = text
                        llm_output = getattr(gen, "generation_info", None) or {}
                        finish_reason = llm_output.get("finish_reason") or finish_reason
                llm_output = getattr(response, "llm_output", None) or {}
                token_usage = llm_output.get("token_usage") or {}
                if not token_usage and generations:
                    # newer core nests usage in the first generation
                    first = generations[0][0]
                    gi = getattr(first, "generation_info", None) or {}
                    token_usage = gi.get("usage_metadata") or {}
                input_tokens = int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0)
                output_tokens = int(token_usage.get("completion_tokens") or token_usage.get("output_tokens") or 0)
            except Exception:
                log.exception("trace: llm usage parse failed")

            if input_tokens == 0 and output_tokens == 0 and response_text:
                # fallback: approximate from character count (~4 chars/token)
                input_tokens = 0
                output_tokens = max(1, len(response_text) // 4)

            await self.store.finish_llm(
                trace_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                response_text=response_text,
                duration_ms=duration_ms,
            )
        except Exception:
            log.exception("trace: llm end failed")

    # ----------------------------------------------------------------- Tool
    async def on_tool_start(self, serialized: dict, input_dict: dict, *,
                            run_id, parent_run_id, **kwargs: Any) -> None:
        try:
            name = serialized.get("name", "unknown")
            trace_id = await self.store.record_tool_start(name, input_dict or {})
            self._tool_start[str(run_id)] = (trace_id, time.time())
        except Exception:
            log.exception("trace: tool start failed")

    async def on_tool_end(self, output, *, run_id, parent_run_id, **kwargs: Any) -> None:
        try:
            marker = self._tool_start.pop(str(run_id), None)
            if not marker:
                return
            trace_id, t0 = marker
            duration_ms = (time.time() - t0) * 1000
            await self.store.finish_tool(
                trace_id, output=output, success=True, error=None, duration_ms=duration_ms,
            )
        except Exception:
            log.exception("trace: tool end failed")

    async def on_tool_error(self, error, *, run_id, parent_run_id, **kwargs: Any) -> None:
        try:
            marker = self._tool_start.pop(str(run_id), None)
            if marker:
                trace_id, t0 = marker
                await self.store.finish_tool(
                    trace_id, output=None, success=False,
                    error=str(error)[:500], duration_ms=(time.time() - t0) * 1000,
                )
        except Exception:
            log.exception("trace: tool error failed")

    # ----------------------------------------------------------------- Misc
    async def on_chain_error(self, error, *, run_id, parent_run_id, tags=None,
                             metadata=None, **kwargs: Any) -> None:
        try:
            err_type = type(error).__name__
            import traceback as _tb

            tb_str = "".join(_tb.format_exception(type(error), error, error.__traceback__))
            await self.store.record_error(
                trace_id=None, error_type=err_type, message=str(error)[:500], traceback_str=tb_str,
            )
        except Exception:
            log.exception("trace: chain error failed")
