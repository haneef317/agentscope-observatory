import { prettyJson } from "../lib/format";

const endpoints = [
  { method: "GET", path: "/api/health", desc: "Backend health, DB and LLM mode status." },
  { method: "GET", path: "/api/agents", desc: "List all registered agents." },
  { method: "POST", path: "/api/agents", desc: "Register a new agent (name, type, model, tools)." },
  { method: "GET", path: "/api/agents/:id/stats", desc: "Run count, avg latency, cost and token aggregates for one agent." },
  { method: "POST", path: "/api/runs/agents/:id/invoke", desc: "Execute an agent with trace capture. Returns 202 with a run id." },
  { method: "GET", path: "/api/runs", desc: "List runs with ?agent_id, ?status, ?search filters." },
  { method: "GET", path: "/api/runs/:id", desc: "Full run detail: LLM requests, tool calls, state transitions, memory updates, errors." },
  { method: "POST", path: "/api/ingest/runs", desc: "Remote ingest: external agents push pre-recorded runs." },
  { method: "GET", path: "/api/stats", desc: "Dashboard analytics: KPIs, time series, top agents, error types." },
  { method: "GET", path: "/api/stats/llm", desc: "Per-model LLM statistics (calls, tokens, cost, latency)." },
];

const wsProto = [
  { event: "run_start", desc: "A new traced run has started." },
  { event: "llm_span", desc: "An LLM call completed: model, tokens, cost, duration, response." },
  { event: "tool_start / tool_end", desc: "Tool invocation lifecycle with input/output JSON." },
  { event: "state_transition", desc: "Graph state snapshot after a node step or checkpoint." },
  { event: "memory_update", desc: "A write to agent memory with old and new values." },
  { event: "error", desc: "An exception occurred inside the run." },
  { event: "run_complete", desc: "Run finished with aggregated duration, tokens and cost." },
];

export default function Docs() {
  return (
    <div>
      <div className="page-header">
        <h1 className="h1" style={{ margin: 0 }}>API & WebSocket reference</h1>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="section-title">REST endpoints</div>
        <table className="data">
          <thead><tr><th>Method</th><th>Path</th><th>Description</th></tr></thead>
          <tbody>
            {endpoints.map((e) => (
              <tr key={e.path}>
                <td><span className={`chip ${e.method === "GET" ? "success" : e.method === "POST" ? "running" : "error"}`}>{e.method}</span></td>
                <td className="mono">{e.path}</td>
                <td className="note">{e.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div className="card">
          <div className="section-title">WebSocket channels</div>
          <table className="data">
            <thead><tr><th>Channel</th><th>Description</th></tr></thead>
            <tbody>
              <tr>
                <td className="mono">/api/ws/runs/{"{run_id}"}</td>
                <td className="note">Live events for a single run. Connect after invoking an agent. If the run already finished, the full trace is replayed from the database before live events start flowing.</td>
              </tr>
              <tr>
                <td className="mono">/api/ws/lifecycle</td>
                <td className="note">Global stream of run start/complete events across all agents.</td>
              </tr>
            </tbody>
          </table>
          <div className="section-title" style={{ marginTop: 14 }}>Event types</div>
          <table className="data">
            <tbody>
              {wsProto.map((w) => (
                <tr key={w.event}>
                  <td className="mono" style={{ whiteSpace: "nowrap" }}>{w.event}</td>
                  <td className="note">{w.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="section-title">Ingest example (Python)</div>
          <pre className="json">{prettyJson({
            agent_id: "my-agent",
            run_name: "user-42 question",
            status: "success",
            duration_ms: 1250,
            total_input_tokens: 420,
            total_output_tokens: 180,
            total_cost_usd: 0.00054,
            llm_requests: [
              { model: "gpt-4o-mini", input_tokens: 400, output_tokens: 180, duration_ms: 900 },
            ],
          })}</pre>
          <div className="note" style={{ marginTop: 10 }}>
            Send it with <code>POST /api/ingest/runs</code> after registering the agent with
            <code> POST /api/agents</code>. The dashboard then displays it like any native run.
          </div>
        </div>
      </div>
    </div>
  );
}
