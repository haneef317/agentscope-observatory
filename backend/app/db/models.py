"""
Observability data model.

Tables
------
agents            : registered agent configurations (graph type, model, …)
runs              : one row per agent execution; aggregates duration/cost/tokens
llm_requests      : every LLM call inside a run (model, tokens, cost, messages)
tool_calls        : every tool invocation (name, input, output, success)
state_transitions : graph state snapshot after each node step
memory_updates    : checkpoint/memory writes captured by the checkpointer
errors            : exceptions raised anywhere inside a run

Indexes are tuned for the dashboard queries: listing recent runs, filtering by
agent/status, and the `/api/stats/*` analytics endpoints.
"""

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class AgentType(str, enum.Enum):
    REACT_AGENT = "react_agent"
    SUPERVISOR = "supervisor"
    CUSTOM = "custom"


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    agent_type = Column(Enum(AgentType), nullable=False, default=AgentType.REACT_AGENT)
    model = Column(String(128), nullable=False, default="gpt-4o-mini")
    system_prompt = Column(Text, nullable=True)
    tools = Column(JSONB, nullable=False, default=list)
    config = Column(JSONB, nullable=False, default=dict)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    runs = relationship("Run", back_populates="agent")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_agent_created", "agent_id", "created_at"),
        Index("ix_runs_status_created", "status", "created_at"),
    )

    id = Column(String(64), primary_key=True)
    agent_id = Column(String(64), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    run_name = Column(String(255), nullable=True)
    status = Column(Enum(RunStatus), nullable=False, default=RunStatus.RUNNING)
    input_payload = Column(JSONB, nullable=True)
    output_summary = Column(Text, nullable=True)

    duration_ms = Column(BigInteger, nullable=True)
    total_input_tokens = Column(BigInteger, nullable=False, default=0)
    total_output_tokens = Column(BigInteger, nullable=False, default=0)
    total_cost_usd = Column(Float, nullable=False, default=0.0)

    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    agent = relationship("Agent", back_populates="runs")
    llm_requests = relationship("LLMRequest", back_populates="run", cascade="all, delete-orphan")
    tool_calls = relationship("ToolCall", back_populates="run", cascade="all, delete-orphan")
    state_transitions = relationship("StateTransition", back_populates="run", cascade="all, delete-orphan")
    memory_updates = relationship("MemoryUpdate", back_populates="run", cascade="all, delete-orphan")
    errors = relationship("RunError", back_populates="run", cascade="all, delete-orphan")


class LLMRequest(Base):
    __tablename__ = "llm_requests"
    __table_args__ = (Index("ix_llm_run_seq", "run_id", "seq"),)

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    trace_id = Column(String(64), nullable=True)
    model = Column(String(128), nullable=False)
    provider = Column(String(64), nullable=True)
    input_tokens = Column(BigInteger, nullable=False, default=0)
    output_tokens = Column(BigInteger, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    duration_ms = Column(BigInteger, nullable=True)
    finish_reason = Column(String(32), nullable=True)
    request_messages = Column(JSONB, nullable=True)
    response_text = Column(Text, nullable=True)
    invocation_params = Column(JSONB, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("Run", back_populates="llm_requests")

    def to_event(self) -> dict:
        """Replay shape mirroring the live ``llm_span`` event from RunStore."""
        return {
            "type": "llm_span",
            "run_id": self.run_id,
            "span_id": self.id,
            "trace_id": self.trace_id,
            "seq": self.seq,
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "finish_reason": self.finish_reason,
            "response_text": self.response_text,
            "request_messages": self.request_messages,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    trace_id = Column(String(64), nullable=True)
    tool_name = Column(String(128), nullable=False)
    input_data = Column(JSONB, nullable=True)
    output_data = Column(JSONB, nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    error = Column(Text, nullable=True)
    duration_ms = Column(BigInteger, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("Run", back_populates="tool_calls")

    def to_event(self) -> dict:
        """Replay shape mirroring the live ``tool_end`` event from RunStore."""
        return {
            "type": "tool_end",
            "run_id": self.run_id,
            "span_id": self.id,
            "trace_id": self.trace_id,
            "tool_name": self.tool_name,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "success": self.success,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class StateTransition(Base):
    __tablename__ = "state_transitions"
    __table_args__ = (Index("ix_state_run_seq", "run_id", "seq"),)

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    step_type = Column(String(32), nullable=False)  # node | checkpoint | task
    node_name = Column(String(128), nullable=True)
    state_snapshot = Column(JSONB, nullable=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now())

    run = relationship("Run", back_populates="state_transitions")

    def to_event(self) -> dict:
        return {
            "type": "state_transition",
            "run_id": self.run_id,
            "seq": self.seq,
            "step_type": self.step_type,
            "node_name": self.node_name,
            "state_snapshot": self.state_snapshot or {},
        }


class MemoryUpdate(Base):
    __tablename__ = "memory_updates"
    __table_args__ = (Index("ix_memory_run_seq", "run_id", "seq"),)

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    source = Column(String(64), nullable=False)  # checkpoint | node
    namespace = Column(String(255), nullable=True)
    memory_key = Column(String(255), nullable=False)
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now())

    run = relationship("Run", back_populates="memory_updates")

    def to_event(self) -> dict:
        return {
            "type": "memory_update",
            "run_id": self.run_id,
            "seq": self.seq,
            "source": self.source,
            "memory_key": self.memory_key,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


class RunError(Base):
    __tablename__ = "run_errors"

    id = Column(String(64), primary_key=True)
    run_id = Column(String(64), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    trace_id = Column(String(64), nullable=True)
    error_type = Column(String(255), nullable=False)
    message = Column(Text, nullable=True)
    traceback = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), server_default=func.now())

    run = relationship("Run", back_populates="errors")

    def to_event(self) -> dict:
        return {
            "type": "error",
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "error_type": self.error_type,
            "message": self.message,
            "traceback": self.traceback,
        }
