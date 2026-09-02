import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AreaChart, BarChart } from "../components/Charts";
import { api, type RunSummary, type Stats } from "../lib/api";
import { fmtMs, fmtRel, fmtTokens, fmtUsd, shortId } from "../lib/format";

const MAX_LABELS = 13;

/** Show at most MAX_LABELS x-axis labels while keeping all data points. */
function thinBuckets(
  data: { bucket: string; value: number }[],
  fmt: (iso: string) => string,
): { bucket: string; value: number }[] {
  if (data.length <= MAX_LABELS) return data.map((d) => ({ bucket: fmt(d.bucket), value: d.value }));
  const step = Math.ceil(data.length / MAX_LABELS);
  return data.map((d, i) => ({
    bucket: i % step === 0 ? fmt(d.bucket) : "",
    value: d.value,
  }));
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let alive = true;
    Promise.all([api.stats(7), api.listRuns({ limit: 8 })])
      .then(([s, r]) => {
        if (alive) { setStats(s); setRecent(r); }
      })
      .catch((e) => alive && setErr(String(e)));
    return () => { alive = false; };
  }, []);

  const kpi = stats?.kpi;
  const fmtBucket = (iso: string) =>
    new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit" });

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="h1" style={{ margin: 0 }}>Dashboard</h1>
          <div className="note">Last 7 days across all agents</div>
        </div>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--red)", marginBottom: 14 }}>
        Could not reach the backend: {err}. Make sure the FastAPI server is running on port 8000.
      </div>}

      {kpi && (
        <div className="grid-kpi">
          <div className="card kpi">
            <div className="label">Total runs</div>
            <div className="value">{kpi.total_runs.toLocaleString()}</div>
          </div>
          <div className="card kpi">
            <div className="label">Total cost</div>
            <div className="value">{fmtUsd(kpi.total_cost_usd)}</div>
            <div className="sub">{fmtTokens(kpi.total_input_tokens)} in · {fmtTokens(kpi.total_output_tokens)} out</div>
          </div>
          <div className="card kpi">
            <div className="label">Avg latency</div>
            <div className="value">{fmtMs(kpi.avg_duration_ms)}</div>
          </div>
          <div className="card kpi">
            <div className="label">Error rate</div>
            <div className="value" style={{ color: kpi.error_rate_pct > 5 ? "var(--red)" : undefined }}>
              {kpi.error_rate_pct.toFixed(1)}%
            </div>
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 14 }}>
        <div className="card">
          <div className="section-title">Runs over time</div>
          <AreaChart data={thinBuckets(stats?.runs_over_time ?? [], fmtBucket)} />
        </div>
        <div className="card">
          <div className="section-title">Cost over time (USD)</div>
          <BarChart data={thinBuckets(stats?.cost_over_time ?? [], fmtBucket)} />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 14, marginTop: 14 }}>
        <div className="card">
          <div className="section-title">Recent runs</div>
          <table className="data">
            <thead>
              <tr>
                <th>Run</th><th>Agent</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Cost</th><th>When</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 && (
                <tr><td colSpan={7} className="empty">No runs yet — open the Playground and invoke the demo agent.</td></tr>
              )}
              {recent.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                  <td className="mono">{shortId(r.id)}</td>
                  <td>{r.run_name ?? r.agent_id}</td>
                  <td><span className={`chip ${r.status}`}>{r.status === "running" && <span className="dot" />}{r.status}</span></td>
                  <td>{fmtMs(r.duration_ms)}</td>
                  <td>{fmtTokens(r.total_input_tokens + r.total_output_tokens)}</td>
                  <td>{fmtUsd(r.total_cost_usd)}</td>
                  <td>{fmtRel(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="card">
            <div className="section-title">Top agents</div>
            <table className="data">
              <thead><tr><th>Agent</th><th>Runs</th><th>Cost</th><th>Avg</th></tr></thead>
              <tbody>
                {(stats?.top_agents ?? []).length === 0 && <tr><td colSpan={4} className="empty">No data</td></tr>}
                {stats?.top_agents.map((a) => (
                  <tr key={a.agent_id} className="clickable" onClick={() => navigate(`/runs?agent_id=${a.agent_id}`)}>
                    <td>{a.agent_id}</td>
                    <td>{a.runs}</td>
                    <td>{fmtUsd(a.cost_usd)}</td>
                    <td>{fmtMs(a.avg_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <div className="section-title">Error types</div>
            {(stats?.error_types ?? []).length === 0 ? (
              <div className="note">No errors recorded. Great!</div>
            ) : (
              stats?.error_types.map((e) => (
                <div key={e.type} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, padding: "4px 0" }}>
                  <span className="mono">{e.type}</span>
                  <span style={{ color: "var(--red)", fontWeight: 600 }}>{e.count}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <div className="note" style={{ marginTop: 16 }}>
        Tip: this dashboard aggregates data stored in PostgreSQL. Live updates for running agents arrive over
        WebSockets — open a <Link className="link" to="/playground">Playground</Link> session to watch a trace build in real time.
      </div>
    </div>
  );
}
