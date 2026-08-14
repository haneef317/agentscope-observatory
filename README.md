# AgentScope Observatory

**An open-source observability platform for LangGraph agents.**

AgentScope Observatory gives you complete visibility into your AI agents: execution tracking, tool call logging, LLM request logging, token usage, cost tracking, latency monitoring, error tracking, state transition visualization, and memory update tracking — all captured automatically from a real LangGraph agent and presented through a simple React dashboard with live WebSocket streaming.

The project ships with two fully instrumented demo agents (a **Research Assistant** and a **Customer Support Bot**) built on LangGraph, so you can explore every feature immediately. When no `OPENAI_API_KEY` is configured, the platform transparently falls back to a **simulator mode** that generates realistic traces (tokens, costs, durations, tool calls, memory writes) with zero LLM cost — ideal for learning, demos, and CI.

> If you are learning these technologies through project-based learning, start with **`GUIDE.md`**. It walks through exactly how this project was designed and built, layer by layer, with the reasoning behind every architecture decision and the hard-won lessons from debugging it.

## Features

| Capability | How it works |
|---|---|
| **Agent execution tracking** | Every agent invocation becomes a `Run` with a unique ID, status lifecycle (`running` → `success`/`failed`), duration, and output summary, stored in PostgreSQL. |
| **Tool call logging** | Each tool invocation is recorded with name, arguments, result, success flag, and duration. |
| **LLM request logging** | Every LLM call captures the full prompt messages, model, parameters, response text, finish reason, and latency. |
| **Token usage** | Input and output token counts are captured per LLM call and rolled up per run. |
| **Cost tracking** | Costs are computed from real model pricing (`app/core/cost.py`) and aggregated per run, per agent, and on the dashboard. |
| **Latency monitoring** | Per-call and per-run durations are measured and visualized as charts and percentiles. |
| **Error tracking** | Exceptions are captured with type, message, and full traceback, visible in the trace and the run view. |
| **State transition visualization** | Every LangGraph checkpoint and node transition is recorded with sequence numbers and rendered as a timeline. |
| **Memory update tracking** | Writes to the agent's memory (notes, conversation history) are logged with source node and old/new values. |
| **Live streaming** | A Redis Pub/Sub fan-out pushes trace events to the browser over WebSockets in real time. |
| **Ingestion API** | `POST /api/ingest/runs` accepts trace payloads from *any* agent or SDK — this platform is not tied to the built-in agents. |

## Architecture

```
┌──────────────────────┐        ┌──────────────────────┐
│  React SPA (Vite)    │        │   FastAPI backend     │
│  Dashboard · Runs    │  HTTP  │                       │
│  Agents · Playground │ ◄────► │  /api/runs   REST     │
│  Docs (interactive)  │   WS   │  /api/stats  REST     │
└──────────────────────┘  ┌────►│  /api/ingest ingest   │
     serves static        │     │  /api/ws     WebSockets
     dist via FastAPI     │     └──────────┬───────────┘
                          │                │ flush() + Redis pub/sub
                          │   ┌────────────▼───────────┐
                          │   │ PostgreSQL  │  Redis   │
                          │   │ runs, spans,│ pub/sub  │
                          │   │ state, mem  │ fan-out  │
                          │   └────────────┴───────────┘
                          │
                          └── LangGraph agents (demo)
                              tracing/handler.py (callback handler)
                              tracing/manager.py (orchestrates runs)
```

The core instrumentation is a **LangChain callback handler** (`backend/app/tracing/handler.py`) attached to the LangGraph `StateGraph`. It receives `on_chat_model_start`, `on_chat_model_end`, `on_tool_start`, `on_tool_end`, `on_chain_end` (state checkpoints), and error events, and persists them to PostgreSQL while publishing the same events to a Redis channel. Each WebSocket connection subscribes to that channel through `RedisPubSub` (`backend/app/core/redis_pubsub.py`), so events reach every open client instantly.

## Quick start

### Prerequisites

- Python 3.12+ and Node.js 20+
- PostgreSQL 16 and Redis 7 (easiest via Docker, see below)

### 1. Run with Docker Compose (recommended)

```bash
cd agentscope
docker compose up --build
```

Open **http://localhost:8000**. The compose file starts PostgreSQL, Redis, and the backend in one command, and runs database migrations automatically on boot.

### 2. Run manually

```bash
# Start dependencies (PostgreSQL + Redis)
docker compose up -d postgres redis

# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env        # edit DATABASE_URL / REDIS_URL as needed
python -m app.db.init_db         # create tables + seed demo agents
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal, hot-reload dev mode)
cd frontend
pnpm install
pnpm dev
# ...or just serve the pre-built bundle: the backend already serves frontend/dist
```

### 3. Try it

1. **Dashboard** — live KPIs: total runs, tokens, cost, latency chart, error rate.
2. **Playground** — pick an agent, type a message, hit **Invoke**. Watch every LLM call, tool call, state transition, and memory update stream in over WebSockets.
3. **Runs** — browse, search, and drill into any run's full trace.
4. **Agents** — register your own agent.
5. **API Docs** — interactive Swagger UI for every endpoint.

### Optional: connect a real LLM

Edit `.env`:

```
OPENAI_API_KEY=sk-...
OPENAI_API_BASE=https://your-openai-compatible-endpoint.example.com/v1   # optional
DEFAULT_MODEL=gpt-4.1-mini
```

Without a key, the platform runs in simulator mode — every feature still works, with synthetic but realistic trace data.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Health check (DB + LLM status) |
| `/api/agents` | GET / POST | List / register agents |
| `/api/agents/{id}` | GET / DELETE | Get / remove agent |
| `/api/agents/{id}/stats` | GET | Per-agent token/cost/latency stats |
| `/api/runs` | GET | List runs (filter by agent, status) |
| `/api/runs/{id}` | GET / DELETE | Run detail with full trace / remove |
| `/api/runs/agents/{id}/invoke` | POST | Invoke an agent (202 + async execution) |
| `/api/ingest/runs` | POST | Ingest a trace from an external agent/SDK |
| `/api/stats` | GET | Dashboard KPIs + time-series buckets |
| `/api/stats/llm` | GET | LLM model breakdown and cost stats |
| `/api/ws/runs/{run_id}` | WebSocket | Live + replayed trace events for one run |
| `/api/ws/lifecycle` | WebSocket | Global stream of run start/complete events |

The backend also serves the compiled React SPA, so one host serves everything.

## Deployment

### Vercel (frontend only, static)

The frontend is a pure Vite SPA, so it deploys to Vercel directly:

```bash
cd frontend
pnpm build                # produces dist/
```

Import the `dist/` folder to Vercel (Drag & Drop or `vercel deploy dist`). **Caveat:** Vercel only hosts static files — you still need a host for the backend and its PostgreSQL/Redis dependencies. In production, set `API_BASE` to your backend URL (the build currently uses a relative path, which only works when the SPA is served by the same origin as the API).

### Backend hosting (Railway / Render / Fly.io)

The backend is a standard FastAPI app, so any Docker-capable host works:

1. Provision a **PostgreSQL** database and a **Redis** instance on the platform.
2. Set the environment variables: `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY` (optional), `DEFAULT_MODEL`, `FRONTEND_DIR`.
3. Deploy using the provided `Dockerfile` in `backend/` (builds the frontend, copies it into the image, runs migrations on boot, starts uvicorn).
4. One service, one URL — the API and the dashboard live on the same origin, so WebSocket connections need no special configuration.

Fly.io example:

```bash
cd backend
fly launch --name agentscope-observatory --dockerfile Dockerfile
fly secrets set DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db" \
              REDIS_URL="rediss://:pass@host:6379"
```

### Local self-hosting

The `docker-compose.yml` at the repository root is the intended self-hosting configuration: it wires PostgreSQL 16, Redis 7, and the backend service (which builds the frontend and serves it) with persistent volumes and automatic migrations.

## Project layout

```
agentscope/
├── README.md / GUIDE.md           # this file / project-based learning guide
├── docker-compose.yml             # one-command self-hosted stack
├── .env.example                   # all configuration knobs
├── backend/
│   ├── Dockerfile                 # production image (builds + serves frontend)
│   ├── requirements.txt
│   └── app/
│       ├── main.py                # FastAPI app, CORS, SPA mount, lifespan
│       ├── api/routes/            # agents, runs, ingest, stats, ws
│       ├── agents/                # demo LangGraph agents (graph.py)
│       ├── core/                  # config, cost model, Redis pub/sub
│       ├── db/                    # SQLAlchemy models, async session, init
│       └── tracing/               # handler.py (callback handler), manager.py
└── frontend/
    ├── vite.config.ts
    └── src/
        ├── pages/                 # Dashboard, Runs, RunDetail, Agents, Playground, Docs
        ├── components/            # custom SVG charts (Charts.tsx), shared UI
        └── lib/api.ts             # typed API client + WebSocket subscriptions
```

## License

MIT — use it, fork it, ship it.
