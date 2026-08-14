"""
Built-in demo agent: a ReAct-style research assistant.

This graph is a fully self-contained LangGraph agent that demonstrates every
observability feature of the platform:

- chat node         → LLM call (captured with tokens + cost)
- decide node       → routes to tool execution or final answer
- tool nodes        → web_search, fetch_content, save_note, read_notes
- memory            → notes list persisted in graph state + checkpointer
- conditional edges → loop back to chat until max_steps or final answer

When `OPENAI_API_KEY` is not configured the agent runs in **simulator mode**:
a deterministic local policy still emits realistic LLM spans (with synthetic
token counts and computed cost), tool calls and memory writes so the dashboard
is fully exercisable without paying for any API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool implementations (local + realistic)
# ---------------------------------------------------------------------------

TOOL_RESPONSES = {
    "web_search": {
        "langgraph agent observability": [
            {"title": "LangGraph Streaming — official docs", "url": "https://docs.langchain.com/oss/python/langgraph/streaming"},
            {"title": "Monitoring token usage for LangGraph agents", "url": "https://example.com/langgraph-tokens"},
        ],
        "default": [
            {"title": "Example result 1", "url": "https://example.com/1"},
            {"title": "Example result 2", "url": "https://example.com/2"},
            {"title": "Example result 3", "url": "https://example.com/3"},
        ],
    },
    "fetch_content": {"default": "The fetched page discusses agent observability, tracing LLM calls, tool usage, token consumption and cost attribution across graph nodes."},
    "save_note": {"default": "Note saved."},
    "read_notes": {"default": []},
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for a query string.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_content",
            "description": "Fetch the textual content of a URL.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save an important finding to long-term memory.",
            "parameters": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notes",
            "description": "Read all notes saved in long-term memory.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ---------------------------------------------------------------------------
# Simulator mode helpers
# ---------------------------------------------------------------------------

def _sim_llm_response(user_text: str, notes: list[str], steps_used: int) -> AIMessage:
    """Deterministic-ish policy that exercises tool calls and memory."""
    lower = user_text.lower()
    tool_calls = []
    if ("search" in lower or "find" in lower or "?" in user_text) and steps_used < 2:
        tool_calls.append({
            "name": "web_search",
            "args": {"query": user_text[:60]},
            "id": f"call_{random.randint(1000, 9999)}",
            "type": "tool_call",
        })
    elif ("note" in lower or "remember" in lower) and steps_used < 2:
        tool_calls.append({
            "name": "save_note",
            "args": {"note": user_text[:120]},
            "id": f"call_{random.randint(1000, 9999)}",
            "type": "tool_call",
        })
    if not tool_calls:
        content = (
            f"Here is what I found about your question. "
            f"I searched, read relevant content, and saved key findings to memory. "
            f"The observability platform tracked every step: LLM calls, tool calls, "
            f"state transitions and memory updates in real time."
        )
    else:
        content = f"Let me look into that. I'll use {', '.join(tc['name'] for tc in tool_calls)}."
    kwargs = {"content": content}
    if tool_calls:
        kwargs["tool_calls"] = tool_calls
    return AIMessage(**kwargs)


def _sim_tool_result(name: str, args: dict, notes: list[str]) -> dict:
    if name == "web_search":
        query = (args.get("query") or "").lower()
        return {"results": TOOL_RESPONSES["web_search"].get(query, TOOL_RESPONSES["web_search"]["default"])}
    if name == "fetch_content":
        return {"content": TOOL_RESPONSES["fetch_content"]["default"]}
    if name == "save_note":
        notes.append(args.get("note", ""))
        return {"saved": True, "total_notes": len(notes)}
    if name == "read_notes":
        return {"notes": list(notes)}
    return {"error": "unknown tool"}


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text)) // 4)


# ---------------------------------------------------------------------------
# Real LLM mode (OpenAI-compatible)
# ---------------------------------------------------------------------------

def _build_chat_model():
    from langchain_openai import ChatOpenAI

    kwargs = {"model": settings.DEFAULT_MODEL, "temperature": 0}
    if settings.OPENAI_API_BASE:
        kwargs["base_url"] = settings.OPENAI_API_BASE
    return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

_graph_cache: dict[str, object] = {}


def build_graph(model: str = "", use_simulator: bool | None = None,
                simulator_store: object | None = None) -> object:
    """Compile the research assistant graph. Cached per (model, simulator) pair.

    `simulator_store` (a RunStore) lets the simulated graph emit realistic
    tool-call and LLM spans so the dashboard is fully populated even without
    a real LLM endpoint.
    """
    if use_simulator is None:
        use_simulator = not settings.real_llm_enabled

    cache_key = f"{model or settings.DEFAULT_MODEL}:{use_simulator}"
    # The simulator graph captures a `store` reference in its node closures.
    # Caching it would bind later runs to a stale RunStore, so only cache
    # graphs built without a store instance.
    if simulator_store is None and cache_key in _graph_cache:
        return _graph_cache[cache_key]

    if use_simulator:
        graph = _build_simulated_graph(store=simulator_store, model=model or settings.DEFAULT_MODEL)
    else:
        graph = _build_real_graph(model or settings.DEFAULT_MODEL)  # noqa: SIM117

    if simulator_store is None:
        _graph_cache[cache_key] = graph
    return graph


def _build_simulated_graph(store=None, model=""):
    """Graph that runs fully locally but emits realistic observability data."""

    from typing import TypedDict

    class AgentState(TypedDict):
        messages: Annotated[list[BaseMessage], add_messages]
        notes: list[str]
        steps_used: int

    memory = MemorySaver()

    async def chat_node(state):
        user_msg = next((m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None)
        notes = state.get("notes") or []
        t0 = time.time()
        if store is not None:
            model_name = model or settings.DEFAULT_MODEL
            msgs = [{"type": "user", "content": user_msg.content}]
            # Synthetic token counts: prompt ≈ input chars/4, response ≈ plan chars/4
            ai = _sim_llm_response(user_msg.content if user_msg else "", notes, state.get("steps_used", 0))
            out_tokens = max(1, len(str(ai.content)) // 4 + sum(len(json.dumps(tc.get("args", {}))) // 4 for tc in (ai.tool_calls or [])))
            in_tokens = max(1, sum(len(m.get("content", "")) // 4 for m in msgs) + len(notes) * 8)
            store.record_llm_start(model=model_name, messages=msgs, params={"model": model_name})
            await store.finish_llm(
                store._llm_trace_ids.pop() if store._llm_trace_ids else None,
                input_tokens=in_tokens, output_tokens=out_tokens,
                finish_reason="stop", response_text=ai.content,
                duration_ms=max(1, int((time.time() - t0) * 1000)),
            )
        else:
            ai = _sim_llm_response(user_msg.content if user_msg else "", notes, state.get("steps_used", 0))
        return {"messages": [ai], "steps_used": state.get("steps_used", 0) + 1}

    async def tools_node(state):
        last = state["messages"][-1]
        notes = list(state.get("notes") or [])
        new_messages = []
        for tc in getattr(last, "tool_calls", []) or []:
            tool_start = time.time()
            if store is not None:
                await store.record_tool_start(tc["name"], tc.get("args", {}))
            result = _sim_tool_result(tc["name"], tc.get("args", {}), notes)
            await asyncio.sleep(0.15)  # simulate latency (never block the loop)
            if store is not None:
                await store.finish_tool(
                    None, output=result, success=True, error=None,
                    duration_ms=max(1, int((time.time() - tool_start) * 1000)),
                )
            new_messages.append(
                {"role": "tool", "content": json.dumps(result), "tool_call_id": tc["id"]}
            )
        out = {"messages": new_messages, "notes": notes}
        return out

    def route(state):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and (last.tool_calls or []):
            return "tools"
        return "end"

    graph = (
        StateGraph(AgentState)
        .add_node("chat", chat_node)
        .add_node("tools", tools_node)
        .add_edge(START, "chat")
        .add_conditional_edges("chat", route, {"tools": "tools", "end": END})
        .add_edge("tools", "chat")
        .compile(checkpointer=memory)
    )
    return graph


def _build_real_graph(model: str):
    """Graph backed by a real OpenAI-compatible LLM with function calling."""

    class AgentState(MessagesState):
        notes: list[str]

    model_inst = _build_chat_model()
    model_with_tools = model_inst.bind_tools(TOOL_SCHEMAS)
    memory = MemorySaver()

    def chat_node(state):
        response = model_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def tools_node(state):
        last = state["messages"][-1]
        notes = list(state.get("notes") or [])
        new_messages = []
        for tc in getattr(last, "tool_calls", []):
            name, args = tc["name"], tc.get("args", {})
            if name == "web_search":
                result = {"results": TOOL_RESPONSES["web_search"].get((args.get("query") or "").lower(), TOOL_RESPONSES["web_search"]["default"])}
            elif name == "fetch_content":
                result = {"content": TOOL_RESPONSES["fetch_content"]["default"]}
            elif name == "save_note":
                notes.append(args.get("note", ""))
                result = {"saved": True, "total_notes": len(notes)}
            elif name == "read_notes":
                result = {"notes": notes}
            else:
                result = {"error": "unknown tool"}
            new_messages.append(
                {"role": "tool", "content": json.dumps(result), "tool_call_id": tc["id"]}
            )
        return {"messages": new_messages, "notes": notes}

    def route(state):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and (last.tool_calls or []):
            return "tools"
        return "end"

    graph = (
        StateGraph(AgentState)
        .add_node("chat", chat_node)
        .add_node("tools", tools_node)
        .add_edge(START, "chat")
        .add_conditional_edges("chat", route, {"tools": "tools", "end": END})
        .add_edge("tools", "chat")
        .compile(checkpointer=memory)
    )
    return graph
