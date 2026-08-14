import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type Agent, type RunSummary } from "../lib/api";
import { fmtMs, fmtRel, fmtTokens, fmtUsd, shortId } from "../lib/format";

export default function Runs() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [params, setParams] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const agentId = params.get("agent_id") ?? "";
  const status = params.get("status") ?? "";
  const search = params.get("search") ?? "";

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.listRuns({ agent_id: agentId || undefined, status: status || undefined, search: search || undefined }),
      api.listAgents(),
    ]).then(([r, a]) => {
      if (alive) { setRuns(r); setAgents(a); setLoading(false); }
    });
    return () => { alive = false; };
  }, [agentId, status, search]);

  const agentName = useMemo(
    () => Object.fromEntries(agents.map((a) => [a.id, a.name])),
    [agents],
  );

  return (
    <div>
      <div className="page-header">
        <h1 className="h1" style={{ margin: 0 }}>Runs</h1>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <select className="input" style={{ width: 190 }} value={agentId}
            onChange={(e) => { const p = new URLSearchParams(params); e.target.value ? p.set("agent_id", e.target.value) : p.delete("agent_id"); setParams(p); }}>
            <option value="">All agents</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
          <select className="input" style={{ width: 140 }} value={status}
            onChange={(e) => { const p = new URLSearchParams(params); e.target.value ? p.set("status", e.target.value) : p.delete("status"); setParams(p); }}>
            <option value="">All statuses</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
            <option value="running">Running</option>
          </select>
          <input className="input" style={{ width: 220 }} placeholder="Search runs…"
            value={search} onChange={(e) => { const p = new URLSearchParams(params); e.target.value ? p.set("search", e.target.value) : p.delete("search"); setParams(p); }} />
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="data">
          <thead>
            <tr>
              <th>Run ID</th><th>Agent</th><th>Summary</th><th>Status</th><th>Duration</th><th>Tokens (in/out)</th><th>Cost</th><th>When</th><th></th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9} className="empty">Loading…</td></tr>}
            {!loading && runs.length === 0 && <tr><td colSpan={9} className="empty">No runs match your filters.</td></tr>}
            {runs.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                <td className="mono">{shortId(r.id)}</td>
                <td>{agentName[r.agent_id] ?? r.agent_id}</td>
                <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.run_name ?? r.output_summary ?? "—"}
                </td>
                <td><span className={`chip ${r.status}`}>{r.status === "running" && <span className="dot" />}{r.status}</span></td>
                <td>{fmtMs(r.duration_ms)}</td>
                <td className="mono">{fmtTokens(r.total_input_tokens)} / {fmtTokens(r.total_output_tokens)}</td>
                <td>{fmtUsd(r.total_cost_usd)}</td>
                <td>{fmtRel(r.started_at)}</td>
                <td>
                  <button className="btn danger" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Delete run ${r.id}?`)) api.deleteRun(r.id).then(() => window.location.reload());
                    }}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
