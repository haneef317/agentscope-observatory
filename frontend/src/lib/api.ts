/**
 * Thin typed client over the FastAPI REST endpoints.
 *
 * All paths are relative so the same code works against the Vite dev proxy
 * (`/api` → localhost:8000) and the production build served by FastAPI.
 */

export interface Agent {
  id: string;
  name: string;
  description: string | null;
  agent_type: string;
  model: string;
  system_prompt: string | null;
  tools: { name: string; description: string }[];
  config: Record<string, unknown>;
  is_demo: boolean;
  created_at: string | null;
}

export interface RunSummary {
  id: string;
  agent_id: string;
  run_name: string | null;
  status: "running" | "success" | "error";
  duration_ms: number | null;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  error: string | null;
  output_summary: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunDetail extends RunSummary {
  input_payload: Record<string, unknown> | null;
  llm_requests: LlmSpan[];
  tool_calls: ToolCall[];
  state_transitions: StateTransition[];
  memory_updates: MemoryUpdate[];
  errors: RunError[];
}

export interface LlmSpan {
  id: string;
  run_id: string;
  seq: number;
  trace_id: string | null;
  model: string;
  provider: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number | null;
  finish_reason: string | null;
  request_messages: unknown[] | null;
  response_text: string | null;
  invocation_params: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ToolCall {
  id: string;
  run_id: string;
  trace_id: string | null;
  tool_name: string;
  input_data: unknown;
  output_data: unknown;
  success: boolean;
  error: string | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface StateTransition {
  id: string;
  run_id: string;
  seq: number;
  step_type: string;
  node_name: string | null;
  state_snapshot: Record<string, unknown> | null;
  occurred_at: string | null;
}

export interface MemoryUpdate {
  id: string;
  run_id: string;
  seq: number;
  source: string;
  namespace: string | null;
  memory_key: string;
  old_value: unknown;
  new_value: unknown;
  occurred_at: string | null;
}

export interface RunError {
  id: string;
  run_id: string;
  trace_id: string | null;
  error_type: string;
  message: string | null;
  traceback: string | null;
  occurred_at: string | null;
}

export interface KPI {
  total_runs: number;
  total_cost_usd: number;
  avg_duration_ms: number;
  error_rate_pct: number;
  total_input_tokens: number;
  total_output_tokens: number;
}

export interface Stats {
  kpi: KPI;
  runs_over_time: { bucket: string; value: number }[];
  cost_over_time: { bucket: string; value: number }[];
  latency_over_time: { bucket: string; value: number }[];
  top_agents: { agent_id: string; runs: number; cost_usd: number; avg_ms: number }[];
  error_types: { type: string; count: number }[];
}

function baseHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { ...baseHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<{ status: string; db: string; llm: string }>("/health"),

  listAgents: () => request<Agent[]>("/agents"),
  getAgent: (id: string) => request<Agent>(`/agents/${id}`),
  createAgent: (body: Partial<Agent>) =>
    request<Agent>("/agents", { method: "POST", body: JSON.stringify(body) }),
  deleteAgent: (id: string) =>
    request<void>(`/agents/${id}`, { method: "DELETE" }),
  agentStats: (id: string) =>
    request<Record<string, number>>(`/agents/${id}/stats`),

  listRuns: (params?: { agent_id?: string; status?: string; search?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.agent_id) qs.set("agent_id", params.agent_id);
    if (params?.status) qs.set("status", params.status);
    if (params?.search) qs.set("search", params.search);
    if (params?.limit) qs.set("limit", String(params.limit));
    return request<RunSummary[]>(`/runs?${qs.toString()}`);
  },
  getRun: (id: string) => request<RunDetail>(`/runs/${id}`),
  deleteRun: (id: string) => request<void>(`/runs/${id}`, { method: "DELETE" }),
  invokeAgent: (agentId: string, message: string, runName?: string) =>
    request<{ run_id: string; status: string }>(`/runs/agents/${agentId}/invoke`, {
      method: "POST",
      body: JSON.stringify({ message, run_name: runName ?? null }),
    }),

  ingestRun: (body: Record<string, unknown>) =>
    request<{ run_id: string; status: string }>("/ingest/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  stats: (days = 7) => request<Stats>(`/stats?days=${days}`),
  llmStats: (days = 7) =>
    request<
      { model: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number; avg_ms: number }[]
    >(`/stats/llm?days=${days}`),
};

export type TraceEvent =
  | { type: "run_start"; run_id: string; agent_id: string; run_name: string | null; started_at: string }
  | { type: "llm_span"; run_id: string; span_id: string; trace_id: string; model: string; provider: string; input_tokens: number; output_tokens: number; cost_usd: number; duration_ms: number; finish_reason: string | null; response_text: string | null; started_at: string; finished_at: string }
  | { type: "tool_start"; run_id: string; span_id: string; trace_id: string; tool_name: string; input_data: unknown; started_at: string }
  | { type: "tool_end"; run_id: string; span_id: string; trace_id: string; tool_name: string; output_data: unknown; success: boolean; error: string | null; duration_ms: number; started_at: string; finished_at: string }
  | { type: "state_transition"; run_id: string; seq: number; step_type: string; node_name: string | null; state_snapshot: Record<string, unknown> | null }
  | { type: "memory_update"; run_id: string; seq: number; source: string; memory_key: string; old_value: unknown; new_value: unknown }
  | { type: "error"; run_id: string; trace_id: string | null; error_type: string; message: string | null; traceback: string | null }
  | { type: "run_complete"; run_id: string; agent_id: string; status: string; duration_ms: number; total_input_tokens: number; total_output_tokens: number; total_cost_usd: number; finished_at: string }
  | { type: "ping" }
  | { type: "system"; message: string };

/**
 * Subscribes to a WebSocket trace channel with automatic reconnect and an
 * event listener registry. Returns an unsubscribe function.
 */
export function subscribeTrace(
  path: string,
  onEvent: (event: TraceEvent) => void,
  opts: { onOpen?: () => void; onClose?: () => void } = {},
): () => void {
  let cancelled = false;
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function connect() {
    if (cancelled) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}${path}`);
    ws.onopen = () => opts.onOpen?.();
    ws.onmessage = (ev) => {
      try {
        onEvent(JSON.parse(ev.data) as TraceEvent);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      opts.onClose?.();
      if (!cancelled) {
        reconnectTimer = setTimeout(connect, 2500);
      }
    };
    ws.onerror = () => ws?.close();
  }

  connect();
  return () => {
    cancelled = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws?.close();
  };
}
