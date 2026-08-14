# The Project-Based Learning Guide

## How AgentScope Observatory was designed and built — layer by layer

This guide exists for one reason: so you can *learn* the technologies behind this project the way they were actually used, including the mistakes made along the way and how they were fixed. Read it alongside the code. Every section points to the exact files to open.

---

## 1. The mental model: what is agent observability?

Before writing a single line of code, understand the problem. An AI agent (a LangGraph state graph) is a program that calls an LLM, uses tools, updates shared state, and loops until done. Every one of those steps is a potential failure point and a potential cost center. Observability means capturing five streams of data for every run:

| Stream | What it records | LangChain callback |
|---|---|---|
| LLM requests | Prompt, response, model, tokens, latency | `on_chat_model_start` / `on_chat_model_end` |
| Tool calls | Name, arguments, result, success | `on_tool_start` / `on_tool_end` |
| State transitions | Checkpoint save, node entry/exit | `on_chain_start` / `on_chain_end` (graph levels) |
| Memory updates | Keys written, old/new values | custom (agent-specific) |
| Errors | Exception type, message, traceback | `on_llm_error` / `on_chain_error` / `on_tool_error` |

**Learning exercise 1:** Open `backend/app/tracing/handler.py` and read the class header comments. Then map each `on_*` method to a row in the table above. This handler is the single most important file in the backend.

---

## 2. Architecture decisions (and why)

### 2.1 FastAPI, not Flask

FastAPI gives you async support (`async def` route handlers), automatic Pydantic validation, and a generated OpenAPI spec — all three matter for a platform whose whole job is ingesting structured events. Flask would have required `asyncio` glue code everywhere.

### 2.2 PostgreSQL for traces, Redis for live delivery

Traces are *records of what happened*. Records belong in a relational store with SQL analytics (`SUM`, `GROUP BY`, time buckets). Live UI delivery is a *messaging* problem: one run's events must fan out to every open browser tab. That is exactly what Redis Pub/Sub is for. Mixing the two (e.g., polling the database from the frontend) works but is slow and wasteful; this design writes once, streams many times.

**Learning exercise 2:** In `backend/app/core/redis_pubsub.py`, trace the flow: `publish(channel, event)` → `Redis.pubsub().subscribe(channel)` → a per-connection `asyncio.Queue`. Notice that each WebSocket connection gets its *own* queue. Why? (Answer: a shared queue would let one slow client block all others.)

### 2.3 The callback handler as the instrumentation seam

LangChain/LangGraph emit lifecycle callbacks to any registered `BaseCallbackHandler`. This means **your agent code never changes** to become observable — you attach the handler to the graph config. This is the same pattern used by LangSmith, LangFuse, and Phoenix. Our handler does two jobs per event:

1. Persist to PostgreSQL (inside a per-run session — see §4 for why "per-run" matters).
2. Publish to Redis for live streaming.

### 2.4 Simulator mode: observability without an LLM bill

The platform must be fully demonstrable with zero API keys. The demo agents in `backend/app/agents/graph.py` normally call OpenAI, but when no key is configured, `_build_simulated_graph` replaces the model node with deterministic logic that generates realistic responses, tool choices, token counts, and costs. Every observability path (handler, database, Redis, WebSocket, UI) is exercised identically — the only difference is where the data originates.

**Design lesson:** when building developer tools, always have a zero-cost demo path. It makes the product explorable, testable, and shareable.

---

## 3. The data model

Open `backend/app/db/models.py`. The model revolves around one entity, `Run`, with five child tables:

```
runs ─┬─ llm_requests     (one per LLM call: messages, params, tokens, cost, latency)
      ├─ tool_calls       (one per tool invocation: name, args, result, success)
      ├─ state_transitions (one per checkpoint/node transition, ordered by seq)
      ├─ memory_updates   (one per memory write: key, old/new value, source node)
      └─ run_errors       (one per exception: type, message, traceback)
```

Three modeling details worth studying:

- **`seq` columns** on llm_requests, state_transitions, and memory_updates preserve call order inside a run. Without them, replaying a trace in the UI would be non-deterministic.
- **`total_*` aggregates** on `runs` (total_input_tokens, total_output_tokens, total_cost_usd) are recomputed at run completion via `func.sum`. This makes dashboard queries trivial (`SELECT total_cost_usd FROM runs`) at the cost of a small write-time aggregation — a classic, worthwhile trade-off.
- **`to_event()` methods** on each model convert a database row back into the exact JSON shape the live stream emits. This is what makes the *replay* feature possible: opening a finished run's WebSocket gets a replay of stored events followed by a live subscription, all through one client code path.

**Learning exercise 3:** Compare the JSON payload in `handler.py`'s `publish(...)` calls with the dictionaries returned by `models.to_event()`. They must be byte-identical — otherwise the frontend would render replayed and live events differently.

---

## 4. Bugs, fixes, and the lessons inside them

This section is the heart of the guide. These are real bugs encountered and fixed during development. Learning from someone else's debugging is how you get better at it fast.

### 4.1 Async session, sync engine — the silent killer

SQLAlchemy 2 async sessions (`AsyncSession`) will fall back to *synchronous* DB access if they detect a sync engine, then crash on `await session.execute(...)`. The fix in `backend/app/db/session.py` is explicit: create the engine with `create_async_engine()` (psycopg3 async driver), verify `is_async` in the lifespan startup, and never mix sync `create_engine` anywhere. **Lesson: in async Python, the engine type and session type must match, and you should assert it at startup.**

### 4.2 The cached-graph-captured-store bug (the worst one)

`build_graph()` cached compiled LangGraph graphs by a string key for performance. The *simulator* graph, however, captures a `store` (a `RunStore` with a live SQLAlchemy session) in its node closures. When the cache returned a previously built graph, a *new* run's events were written through the *old* run's session — producing mysteriously zero token totals, or data attributed to the wrong run.

The fix in `backend/app/agents/graph.py`: **never cache a graph that carries a per-run resource**. The cache key was narrowed to `model:use_simulator`, and the cache is only consulted when `simulator_store is None`.

**Lesson: closures capture references, not values. Any cached object that closes over per-request state (sessions, contexts, request-scoped singletons) will silently corrupt later requests. When in doubt, rebuild.**

### 4.3 The fallback-retry cleanup gap

When the real LLM call fails (e.g., no API key), the platform retries the run in simulator mode. The first (failed) attempt had already written error rows and — in earlier revisions — a stray LLM request row with zero tokens. The retry's aggregate then summed stale rows, producing wrong totals. The fix in `backend/app/tracing/manager.py`: on fallback, delete *all* previously written rows for the run (errors, llm, tool, state, memory) and reset the store's sequence counters before re-running.

**Lesson: any "retry" path that re-executes instrumented code must first undo the instrumentation side effects of the failed attempt.**

### 4.4 WebSocket URL mismatches

The backend mounted WebSocket routes at `/api/ws/...`, but early frontend code connected to `/ws/...` (missing the API prefix used by every REST endpoint), causing 403/connection-rejected errors that looked like auth problems. The fix: derive the WebSocket URL from the same `API_BASE` constant the REST client uses, so REST and WS can never drift apart.

**Lesson: keep your API base URL in exactly one constant and derive everything from it.**

### 4.5 Vite 8 / Rolldown type-only import errors and Recharts v3 crashes

Two frontend build/runtime failures, both from very new dependency versions: Vite 8's new bundler (Rolldown) rejected certain `import type` patterns, and Recharts v3 crashed at runtime under the project's configuration. Fixes: pin import syntax accordingly, and replace the charting library entirely with **hand-rolled SVG components** (`frontend/src/components/Charts.tsx`, ~150 lines). The result: zero extra dependencies, full control over styling, and charts that match the design exactly.

**Lesson: a small, purpose-built component is often better than a heavy library — and newer major versions of dependencies are the most common source of sudden breakage. Pin versions.**

### 4.6 Chart bucket overflow

The stats endpoint initially returned 48 hourly buckets; the UI crammed all 48 labels onto the x-axis, producing unreadable text. Two-sided fix: the backend caps buckets at ~24, and the frontend thins x-axis labels further (`thinBuckets` helper in `Dashboard.tsx`).

**Lesson: never assume the server's page size is a reasonable UI count. UIs should thin; servers should cap.**

---

## 5. How the live trace works, end to end

Trace one `Invoke` request from button to browser. This is the core of the product.

1. **Frontend** (`Playground.tsx`): user hits Invoke → `POST /api/runs/agents/{id}/invoke` → backend returns `202 Accepted` with a `run_id` → frontend opens a WebSocket to `/api/ws/runs/{run_id}`.
2. **Backend** (`runs.py`): a `Run` row is inserted with status `running`; a `run_start` event is published to the `run:lifecycle` Redis channel; execution is dispatched to an asyncio background task (so the HTTP response returns immediately).
3. **Agent executes** (`manager.py` + `graph.py`): the LangGraph graph runs with the callback handler attached. Each node transition, LLM call, tool call, and memory write calls into the handler.
4. **Handler** (`handler.py`): every event is (a) persisted via the per-run `RunStore` and (b) published to the Redis channel `run:{run_id}`.
5. **Pub/Sub bridge** (`ws.py`): the WebSocket connection subscribes to the channel; each arriving event is `send_json`'d straight to the browser.
6. **Frontend** renders each event into the live trace panel, with the final `run_complete` event showing totals and a link to the full run view.

If you open a *finished* run's detail page, the same WebSocket receives a **replay** first (rows converted via `to_event()`, in sequence order) and then — if the run were still active — live events. One client, one code path, two data sources.

---

## 6. The frontend: small and deliberate

The dashboard is deliberately simple: five pages (Dashboard, Runs, RunDetail, Agents, Playground) plus an API Docs page. Key choices:

- **One API client** (`frontend/src/lib/api.ts`) with typed request/response shapes. All pages import from it; nothing fetches directly.
- **Custom SVG charts** instead of a charting library — see §4.5. `Charts.tsx` exposes `AreaChart` and `BarChart` primitives that take raw data and emit SVG; the pages only format labels.
- **Deterministic event styling**: each event type (`llm`, `tool`, `state`, `memory`, `error`, `run`) maps to a color and icon, so the trace reads like a story.
- **SPA served by the backend**: FastAPI mounts `frontend/dist` as static files and falls back to `index.html` for all non-API paths, so the whole product deploys as one service.

---

## 7. Suggested learning path (project-based)

Work through the project in this order. Each step is sized for one sitting (1–3 hours).

| Step | Task | Files to study / modify |
|---|---|---|
| 1 | Run the stack via Docker and explore every page. Invoke both agents. | `docker-compose.yml`, README |
| 2 | Read the callback handler end to end. Annotate what each `on_*` method does. | `backend/app/tracing/handler.py` |
| 3 | Read the data model and write a hand-drawn ER diagram. | `backend/app/db/models.py` |
| 4 | Add a new field: `tool_calls.latency_p50` or `llm_requests.cache_hit`. Migrate, populate, show it in the UI. | `models.py`, `handler.py`, `RunDetail.tsx` |
| 5 | Add a third demo agent (e.g., a "summarizer" with a memory-write node) and register it via the Agents page. | `backend/app/agents/graph.py` |
| 6 | Ingest an external trace: POST a synthetic payload to `/api/ingest/runs` and verify it appears. | `backend/app/api/routes/ingest.py` |
| 7 | Break something deliberately: set a bad `REDIS_URL`, watch the failure mode, then read how the app degrades (simulator + queued-local fallback). | `main.py` lifespan |
| 8 | Read the migration from Recharts to custom SVG: rebuild `Charts.tsx` from scratch yourself. | `frontend/src/components/Charts.tsx` |
| 9 | Reproduce and re-fix the cached-graph bug: reintroduce graph caching for simulator graphs, watch totals go wrong, apply the §4.2 fix. | `backend/app/agents/graph.py` |
| 10 | Deploy to a real host (Fly.io/Railway + managed Postgres/Redis). Change `OPENAI_API_KEY` and watch the platform switch from simulator to real LLM traces. | README § Deployment |

**A note on step 7:** the platform is designed to degrade gracefully. If PostgreSQL is unreachable, REST endpoints return clear errors and the WebSocket bridge degrades to in-process delivery for the current connection; if Redis is down, events still persist to the database and the UI can reload them on demand. Engineering degradation paths is what separates demo code from production code.

---
