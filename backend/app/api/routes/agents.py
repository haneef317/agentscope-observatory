"""Agent registry REST endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentType, Run
from app.db.session import get_db
from app.db.init_db import gen_id
from app.api.schemas import AgentCreate, AgentOut

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).order_by(Agent.created_at.desc()))
    return result.scalars().all()


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    return agent


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db)):
    try:
        agent_type = AgentType(body.agent_type)
    except ValueError:
        raise HTTPException(400, f"Invalid agent_type: {body.agent_type}")
    agent = Agent(
        id=gen_id("agent"),
        name=body.name,
        description=body.description,
        agent_type=agent_type,
        model=body.model,
        system_prompt=body.system_prompt,
        tools=body.tools,
        config=body.config,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    if agent.is_demo:
        raise HTTPException(400, "The demo agent cannot be deleted")
    await db.delete(agent)
    await db.commit()


@router.get("/{agent_id}/stats")
async def agent_stats(agent_id: str, db: AsyncSession = Depends(get_db)):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    agg = await db.execute(
        select(
            func.count(Run.id).label("runs"),
            func.avg(Run.duration_ms).label("avg_ms"),
            func.sum(Run.total_cost_usd).label("cost"),
            func.sum(Run.total_input_tokens).label("in"),
            func.sum(Run.total_output_tokens).label("out"),
        ).where(Run.agent_id == agent_id)
    )
    row = agg.one()
    return {
        "agent_id": agent_id,
        "runs": int(row.runs or 0),
        "avg_duration_ms": float(row.avg_ms or 0),
        "total_cost_usd": float(row.cost or 0),
        "total_input_tokens": int(row.in_ or 0),
        "total_output_tokens": int(row.out_ or 0),
    }
