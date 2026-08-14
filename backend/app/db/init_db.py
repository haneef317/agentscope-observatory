"""
Database initialization: create tables and seed the demo agent.

Idempotent — safe to call on every process start. Tables are created with
`CREATE TABLE IF NOT EXISTS` so existing data is never touched.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Agent, AgentType, Base

log = logging.getLogger(__name__)


async def init_tables(session: AsyncSession) -> None:
    from app.db.session import engine

    await session.execute(text("SELECT 1"))  # verify connectivity
    # `session.run_sync` is not compatible with psycopg's sync Session wrapper,
    # and creating a brand-new synchronous engine inside a running event loop
    # fails for the psycopg3 driver, so DDL is created on the AsyncEngine's
    # underlying sync engine (same URL, reuses the driver, no new connections).
    # Must run in a worker thread: calling the sync metadata.create_all
    # directly on the async engine's sync_engine from an async context hits
    # SQLAlchemy's greenlet guard (MissingGreenlet). A fresh sync engine is
    # created from the raw settings URL (not session.get_bind(), whose
    # string form masks the password) so the psycopg3 driver can connect.
    def _create_tables() -> None:
        from sqlalchemy import create_engine as _ce

        sync_engine = _ce(settings.DATABASE_URL)
        Base.metadata.create_all(sync_engine)
        sync_engine.dispose()

    await asyncio.to_thread(_create_tables)


async def seed_demo_agent(session: AsyncSession) -> str:
    """Create the built-in research assistant agent if it does not exist."""
    agent_id = "demo-research-assistant"
    existing = await session.execute(select(Agent).where(Agent.id == agent_id))
    if existing.scalar_one_or_none():
        return agent_id

    session.add(
        Agent(
            id=agent_id,
            name="Research Assistant",
            description=(
                "Built-in demo agent: a ReAct-style research assistant that "
                "searches the web, reads content, keeps notes in memory, and "
                "answers questions. It ships with the platform so you can try "
                "all observability features without any API key."
            ),
            agent_type=AgentType.REACT_AGENT,
            model="gpt-4o-mini",
            system_prompt=(
                "You are a helpful research assistant. Use the available tools "
                "to gather information before answering. Keep notes in memory "
                "when you learn something important."
            ),
            tools=[
                {"name": "web_search", "description": "Search the web for a query"},
                {"name": "fetch_content", "description": "Fetch the content of a URL"},
                {"name": "save_note", "description": "Save a note to long-term memory"},
                {"name": "read_notes", "description": "Read all saved notes"},
            ],
            config={"max_steps": 10, "recursion_limit": 25},
            is_demo=True,
        )
    )
    await session.commit()
    log.info("Seeded demo agent %s", agent_id)
    return agent_id


def gen_id(prefix: str = "") -> str:
    return (f"{prefix}-" if prefix else "") + uuid.uuid4().hex[:16]
