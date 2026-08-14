"""
Analytics endpoints powering the dashboard: KPIs, time series, top agents,
error breakdown and per-agent latency percentiles.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import KPI, StatsOut
from app.db.models import LLMRequest, Run, RunError, RunStatus
from app.db.session import get_db

router = APIRouter(prefix="/stats", tags=["stats"])


def _buckets(days: int, width_hours: float):
    """Generate aligned time buckets ending now."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    buckets, current = [], start
    while current < now:
        buckets.append(current)
        current += timedelta(hours=width_hours)
    return buckets


@router.get("")
async def dashboard_stats(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base_q = Run.created_at >= cutoff

    # ---------------- KPI
    agg = await db.execute(
        select(
            func.count(Run.id).label("n"),
            func.sum(Run.total_cost_usd).label("cost"),
            func.avg(Run.duration_ms).label("avg_ms"),
            func.sum(Run.total_input_tokens).label("tok_in"),
            func.sum(Run.total_output_tokens).label("tok_out"),
        ).where(base_q)
    )
    row = agg.one()
    n = int(row.n or 0)
    err_agg = await db.execute(
        select(func.count(Run.id)).where(base_q, Run.status == RunStatus.ERROR)
    )
    n_err = int(err_agg.scalar() or 0)
    kpi = KPI(
        total_runs=n,
        total_cost_usd=float(row.cost or 0),
        avg_duration_ms=float(row.avg_ms or 0),
        error_rate_pct=round(100 * n_err / n, 2) if n else 0.0,
        total_input_tokens=int(row.tok_in or 0),
        total_output_tokens=int(row.tok_out or 0),
    )

    # ---------------- Time series (6h buckets over `days`)
    width_hours = max(1.0, days * 24 / 48)
    bucket_expr = func.date_trunc("hour", Run.created_at)
    series = {}
    for name, metric in (("runs", func.count(Run.id)), ("cost", func.sum(Run.total_cost_usd)),
                         ("latency", func.avg(Run.duration_ms))):
        res = await db.execute(
            select(bucket_expr.label("b"), metric.label("v"))
            .where(base_q).group_by("b").order_by("b")
        )
        series[name] = {b.isoformat(): (float(v) if v is not None else 0) for b, v in res.all()}
    buckets = _buckets(days, width_hours)
    runs_over_time = [{"bucket": b.isoformat(), "value": series["runs"].get(b.isoformat(), 0)} for b in buckets]
    cost_over_time = [{"bucket": b.isoformat(), "value": round(series["cost"].get(b.isoformat(), 0), 6)} for b in buckets]
    latency_over_time = [{"bucket": b.isoformat(), "value": round(series["latency"].get(b.isoformat(), 0), 1)} for b in buckets]

    # ---------------- Top agents
    res = await db.execute(
        select(
            Run.agent_id, func.count(Run.id).label("n"),
            func.sum(Run.total_cost_usd).label("cost"), func.avg(Run.duration_ms).label("avg"),
        ).where(base_q).group_by(Run.agent_id).order_by(func.count(Run.id).desc()).limit(10)
    )
    top_agents = [{"agent_id": r, "runs": int(n), "cost_usd": float(c or 0), "avg_ms": float(a or 0)}
                  for r, n, c, a in res.all()]

    # ---------------- Error types
    res = await db.execute(
        select(RunError.error_type, func.count(RunError.id))
        .where(RunError.occurred_at >= cutoff).group_by(RunError.error_type).order_by(func.count(RunError.id).desc()).limit(10)
    )
    error_types = [{"type": t, "count": int(c)} for t, c in res.all()]

    return StatsOut(
        kpi=kpi,
        runs_over_time=runs_over_time,
        cost_over_time=cost_over_time,
        latency_over_time=latency_over_time,
        top_agents=top_agents,
        error_types=error_types,
    )


@router.get("/llm")
async def llm_stats(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    res = await db.execute(
        select(
            LLMRequest.model,
            func.count(LLMRequest.id).label("calls"),
            func.sum(LLMRequest.input_tokens).label("tok_in"),
            func.sum(LLMRequest.output_tokens).label("tok_out"),
            func.sum(LLMRequest.cost_usd).label("cost"),
            func.avg(LLMRequest.duration_ms).label("avg_ms"),
        ).where(LLMRequest.started_at >= cutoff).group_by(LLMRequest.model).order_by(func.count(LLMRequest.id).desc())
    )
    return [
        {
            "model": r, "calls": int(c), "input_tokens": int(tok_in or 0),
            "output_tokens": int(tok_out or 0), "cost_usd": float(cu or 0), "avg_ms": float(a or 0),
        }
        for r, c, tok_in, tok_out, cu, a in res.all()
    ]
