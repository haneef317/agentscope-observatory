"""Request/response schemas for the REST API."""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    agent_type: str = "react_agent"
    model: str = "gpt-4o-mini"
    system_prompt: str | None = None
    tools: list[dict] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)


class AgentOut(BaseModel):
    id: str
    name: str
    description: str | None
    agent_type: str
    model: str
    system_prompt: str | None
    tools: list[dict]
    config: dict
    is_demo: bool
    created_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class RunSummary(BaseModel):
    id: str
    agent_id: str
    run_name: str | None
    status: str
    duration_ms: int | None
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    error: str | None
    output_summary: str | None
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    input_payload: dict | None
    llm_requests: list[dict]
    tool_calls: list[dict]
    state_transitions: list[dict]
    memory_updates: list[dict]
    errors: list[dict]


class InvokeRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    run_name: str | None = None


# ---------------------------------------------------------------------------
# Ingest (external agents)
# ---------------------------------------------------------------------------

class IngestRun(BaseModel):
    run_id: str | None = None
    run_name: str | None = None
    status: str = "success"  # success | error
    input: dict | None = None
    output: str | None = None
    duration_ms: int | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    started_at: str | None = None
    llm_requests: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    state_transitions: list[dict] = Field(default_factory=list)
    memory_updates: list[dict] = Field(default_factory=list)


class IngestResponse(BaseModel):
    run_id: str
    status: str


# ---------------------------------------------------------------------------
# Stats / dashboard
# ---------------------------------------------------------------------------

class KPI(BaseModel):
    total_runs: int
    total_cost_usd: float
    avg_duration_ms: float
    error_rate_pct: float
    total_input_tokens: int
    total_output_tokens: int


class SeriesPoint(BaseModel):
    bucket: str
    value: float


class StatsOut(BaseModel):
    kpi: KPI
    runs_over_time: list[dict]
    cost_over_time: list[dict]
    latency_over_time: list[dict]
    top_agents: list[dict]
    error_types: list[dict]
