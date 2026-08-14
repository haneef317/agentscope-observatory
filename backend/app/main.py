"""
AgentScope Observatory — FastAPI application entry point.

Lifecycle
---------
1. On startup: connect Redis (fallback to in-memory bus), create DB tables,
   seed the demo agent, and build the TracingManager.
2. Serves the REST API under /api and the compiled React frontend at /.
3. On shutdown: dispose the engine and close Redis.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import agents, ingest, runs, stats, ws
from app.core.config import settings
from app.core.redis_pubsub import InMemoryBus, RedisPubSub
from app.db.init_db import init_tables, seed_demo_agent
from app.db.session import async_session_factory, engine
from app.tracing.manager import TracingManager

_memory_bus = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global _memory_bus

    # --- Redis (optional): falls back to an in-memory broadcast bus
    pubsub = RedisPubSub(settings.REDIS_URL)
    redis_ok = await pubsub.connect()
    if not redis_ok:
        _memory_bus = InMemoryBus()
        original_publish = pubsub.publish

        async def fallback_publish(channel: str, payload: dict) -> None:
            await _memory_bus.publish(channel, payload)
            try:
                await original_publish(channel, payload)
            except Exception:
                pass

        pubsub.publish = fallback_publish
        # Patch subscribe to use in-memory queues when Redis is absent.
        async def fallback_subscribe(channel: str, queue) -> None:
            _memory_bus.subscribe(channel, queue)

        pubsub.subscribe = fallback_subscribe
        async def fallback_unsubscribe(channel: str) -> None:
            _memory_bus.unsubscribe(channel, None)

        pubsub.unsubscribe = fallback_unsubscribe

    # --- PostgreSQL (optional): demo keeps working without it
    db_available = False
    try:
        async with async_session_factory() as session:
            await init_tables(session)
            await seed_demo_agent(session)
            db_available = True
    except Exception as exc:
        application.state.db_error = str(exc)
        print(f"[agentscope] database unavailable, running in demo-only mode: {exc}")

    application.state.pubsub = pubsub
    application.state.tracing_manager = TracingManager(pubsub)
    application.state.db_available = db_available

    yield

    try:
        await engine.dispose()
    except Exception:
        pass


app = FastAPI(
    title="AgentScope Observatory",
    description="Open-source observability platform for LangGraph agents",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------ REST API
app.include_router(agents.router, prefix=settings.API_PREFIX)
app.include_router(runs.router, prefix=settings.API_PREFIX)
app.include_router(ingest.router, prefix=settings.API_PREFIX)
app.include_router(stats.router, prefix=settings.API_PREFIX)
app.include_router(ws.router, prefix=settings.API_PREFIX)


@app.get(f"{settings.API_PREFIX}/health")
async def health():
    return {
        "status": "ok",
        "db": "connected" if app.state.db_available else "unavailable",
        "llm": "real" if settings.real_llm_enabled else "simulator",
    }


# ------------------------------------------------------- Frontend (static)
import os

_frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", settings.FRONTEND_DIR)
if os.path.isdir(_frontend_dir) and os.path.isfile(os.path.join(_frontend_dir, "index.html")):
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dir, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Catch-all: serve index.html for any non-API path (SPA routing)."""
        if full_path.startswith("api") or full_path.startswith("ws"):
            from fastapi import HTTPException

            raise HTTPException(404)
        file_path = os.path.join(_frontend_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_frontend_dir, "index.html"))
