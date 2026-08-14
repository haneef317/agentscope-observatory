import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type RunDetail as RunDetailT, type TraceEvent } from "../lib/api";
import { fmtMs, fmtTime, fmtTokens, fmtUsd, prettyJson, shortId, truncate } from "../lib/format";

type Tab = "timeline" | "llm" | "tools" | "state" | "memory" | "errors" | "raw";

function JsonView({ data, title }: { data: unknown; title?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      {title && <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 6, fontWeight: 600 }}>{title}</div>}
      <pre className="json">{prettyJson(data)}</pre>
    </div>
  );
}

function Detail({ run }: { run: RunDetailT }) {
  const [tab, setTab] = useState<Tab>("timeline");
  const [selectedSpan, setSelectedSpan] = useState<string | null>(null);
  const [selectedTool, setSelectedTool] = useState<string | null>(null);

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "timeline", label: "Timeline" },
    { id: "llm", label: "LLM Requests", count: run.llm_requests.length },
    { id: "tools", label: "Tool Calls", count: run.tool_calls.length },
    { id: "state", label: "State Transitions", count: run.state_transitions.length },
    { id: "memory", label: "Memory Updates", count: run.memory_updates.length },
    { id: "errors", label: "Errors", count: run.errors.length },
    { id: "raw", label: "Raw JSON" },
  ];

  const totalSpanMs = useMemo(() => {
    let maxEnd = 0;
    for (const s of run.llm_requests) if (s.duration_ms) maxEnd = Math.max(maxEnd, s.duration_ms);
    for (const t of run.tool_calls) if (t.duration_ms) maxEnd = Math.max(maxEnd, t.duration_ms);
    return Math.max(1, maxEnd, run.duration_ms ?? 1);
  }, [run]);

  return (
    <div>
      <div className="page-header">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1 className="h1" style={{ margin: 0, fontSize: 18 }}>
              <span className="mono" style={{ fontSize: 13, color: "var(--text-dim)" }}>{run.id}</span>
            </h1>
            <span className={`chip ${run.status}`}>{run.status === "running" && <span className="dot" />}{run.status}</span>
          </div>
          <div className="note" style={{ marginTop: 4 }}>
            Agent <b>{run.agent_id}</b> · started {fmtTime(run.started_at)}
            {run.run_name && <> · <b>{run.run_name}</b></>}
            {run.output_summary && <> — {truncate(run.output_summary, 200)}</>}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span className="card" style={{ padding: "8px 14px" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Duration </span>
            <b>{fmtMs(run.duration_ms)}</b>
          </span>
          <span className="card" style={{ padding: "8px 14px" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Tokens </span>
            <b>{fmtTokens(run.total_input_tokens)} in / {fmtTokens(run.total_output_tokens)} out</b>
          </span>
          <span className="card" style={{ padding: "8px 14px" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Cost </span>
            <b>{fmtUsd(run.total_cost_usd)}</b>
          </span>
        </div>
      </div>

      {run.error && (
        <div className="card" style={{ borderColor: "var(--red)", marginBottom: 14 }}>
          <div style={{ fontWeight: 600, color: "var(--red)" }}>Run failed</div>
          <pre className="json" style={{ marginTop: 8 }}>{run.error}</pre>
        </div>
      )}

      <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        {tabs.map((t) => (
          <button key={t.id} className={`btn ${tab === t.id ? "" : "secondary"}`} style={{ opacity: tab === t.id ? 1 : 0.75 }} onClick={() => setTab(t.id)}>
            {t.label}
            {t.count !== undefined && <span className="badge-count">{t.count}</span>}
          </button>
        ))}
      </div>

      {/* ---------------- Timeline ------------------------------------ */}
      {tab === "timeline" && (
        <div className="card">
          <div className="section-title">Span waterfall (LLM calls and tool calls)</div>
          {run.llm_requests.length === 0 && run.tool_calls.length === 0 ? (
            <div className="empty">No spans recorded for this run.</div>
          ) : (
            <div className="timeline">
              {run.llm_requests.map((s) => (
                <div key={s.id} className="tl-row">
                  <div className="mono" style={{ color: "var(--accent)", cursor: "pointer" }}
                    onClick={() => setSelectedSpan(selectedSpan === s.id ? null : s.id)}>
                    LLM · {s.model}
                  </div>
                  <div className="tl-bar-wrap">
                    <div className="tl-bar llm" style={{ width: `${Math.max(3, ((s.duration_ms ?? 0) / totalSpanMs) * 100)}%`, cursor: "pointer" }}
                      onClick={() => setSelectedSpan(selectedSpan === s.id ? null : s.id)}>
                      {fmtMs(s.duration_ms)} · {fmtTokens(s.input_tokens + s.output_tokens)} tok
                    </div>
                  </div>
                  <div style={{ textAlign: "right", color: "var(--text-dim)" }}>{fmtUsd(s.cost_usd)}</div>
                </div>
              ))}
              {run.tool_calls.map((t) => (
                <div key={t.id} className="tl-row">
                  <div className="mono" style={{ color: t.success ? "var(--green)" : "var(--red)", cursor: "pointer" }}
                    onClick={() => setSelectedTool(selectedTool === t.id ? null : t.id)}>
                    TOOL · {t.tool_name}
                  </div>
                  <div className="tl-bar-wrap">
                    <div className={`tl-bar ${t.success ? "tool" : "err"}`} style={{ width: `${Math.max(3, ((t.duration_ms ?? 0) / totalSpanMs) * 100)}%`, cursor: "pointer" }}
                      onClick={() => setSelectedTool(selectedTool === t.id ? null : t.id)}>
                      {fmtMs(t.duration_ms)}
                    </div>
                  </div>
                  <div style={{ textAlign: "right", color: "var(--text-dim)" }}>{t.success ? "ok" : "failed"}</div>
                </div>
              ))}
            </div>
          )}
          {selectedSpan && (() => {
            const s = run.llm_requests.find((x) => x.id === selectedSpan);
            return s && <JsonView title={`LLM span ${shortId(s.id)} — ${s.model}`} data={s} />;
          })()}
          {selectedTool && (() => {
            const t = run.tool_calls.find((x) => x.id === selectedTool);
            return t && <JsonView title={`Tool call ${t.tool_name}`} data={t} />;
          })()}
        </div>
      )}

      {/* ---------------- LLM requests -------------------------------- */}
      {tab === "llm" && (
        <div className="card">
          {run.llm_requests.length === 0 && <div className="empty">No LLM requests recorded.</div>}
          {run.llm_requests.map((s) => (
            <div key={s.id} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 14, marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
                <div><b>{s.model}</b> <span className="chip running" style={{ marginLeft: 8 }}>{s.provider ?? "?"}</span></div>
                <div className="note mono">
                  {fmtTime(s.started_at)} → {fmtTime(s.finished_at)} · {fmtMs(s.duration_ms)}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8, marginBottom: 10 }}>
                <span className="card" style={{ padding: 8 }}>in <b>{fmtTokens(s.input_tokens)}</b></span>
                <span className="card" style={{ padding: 8 }}>out <b>{fmtTokens(s.output_tokens)}</b></span>
                <span className="card" style={{ padding: 8 }}>cost <b>{fmtUsd(s.cost_usd)}</b></span>
                <span className="card" style={{ padding: 8 }}>finish <b>{s.finish_reason ?? "—"}</b></span>
              </div>
              <JsonView title="Request messages" data={s.request_messages} />
              <JsonView title="Response" data={s.response_text} />
            </div>
          ))}
        </div>
      )}

      {/* ---------------- Tool calls ---------------------------------- */}
      {tab === "tools" && (
        <div className="card">
          {run.tool_calls.length === 0 && <div className="empty">No tool calls recorded.</div>}
          <table className="data">
            <thead><tr><th>Tool</th><th>Success</th><th>Duration</th><th>When</th></tr></thead>
            <tbody>
              {run.tool_calls.map((t) => (
                <tr key={t.id} className="clickable" onClick={() => setSelectedTool(selectedTool === t.id ? null : t.id)}>
                  <td className="mono">{t.tool_name}</td>
                  <td><span className={`chip ${t.success ? "success" : "error"}`}>{t.success ? "ok" : "failed"}</span></td>
                  <td>{fmtMs(t.duration_ms)}</td>
                  <td className="note">{fmtTime(t.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selectedTool && (() => {
            const t = run.tool_calls.find((x) => x.id === selectedTool);
            return t && <JsonView title={`Tool ${t.tool_name} — full input/output`} data={t} />;
          })()}
        </div>
      )}

      {/* ---------------- State transitions --------------------------- */}
      {tab === "state" && (
        <div className="card">
          <div className="note" style={{ marginBottom: 10 }}>
            Each row is a graph state snapshot captured after a node step or a checkpointer write. In the LangGraph
            visualizer these would appear as nodes lighting up along the edges of your graph.
          </div>
          {run.state_transitions.length === 0 && <div className="empty">No state transitions recorded.</div>}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {run.state_transitions.map((st) => (
              <div key={st.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span>
                    <span className="chip running" style={{ marginRight: 8 }}>{st.step_type}</span>
                    <b className="mono">{st.node_name ?? "(checkpoint)"}</b>
                    <span className="note" style={{ marginLeft: 8 }}>seq {st.seq} · {fmtTime(st.occurred_at)}</span>
                  </span>
                </div>
                <pre className="json" style={{ marginTop: 8, maxHeight: 180 }}>
                  {prettyJson(st.state_snapshot)}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---------------- Memory updates ------------------------------ */}
      {tab === "memory" && (
        <div className="card">
          <div className="note" style={{ marginBottom: 10 }}>
            Memory updates are writes to the agent's persisted memory (graph checkpointer or node-level memory keys).
            Each row shows the value before and after the write.
          </div>
          {run.memory_updates.length === 0 && <div className="empty">No memory updates recorded in this run.</div>}
          {run.memory_updates.map((m) => (
            <div key={m.id} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, marginBottom: 10 }}>
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <b className="mono">{m.memory_key}</b>
                <span className="chip running" style={{ marginLeft: 8 }}>{m.source}</span>
                <span className="note" style={{ marginLeft: 8 }}>seq {m.seq} · {fmtTime(m.occurred_at)}</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <div className="note" style={{ marginBottom: 4 }}>old value</div>
                  <pre className="json" style={{ maxHeight: 150 }}>{prettyJson(m.old_value)}</pre>
                </div>
                <div>
                  <div className="note" style={{ marginBottom: 4 }}>new value</div>
                  <pre className="json" style={{ maxHeight: 150 }}>{prettyJson(m.new_value)}</pre>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ---------------- Errors -------------------------------------- */}
      {tab === "errors" && (
        <div className="card">
          {run.errors.length === 0 ? (
            <div className="empty">No errors for this run. 🎉</div>
          ) : run.errors.map((e) => (
            <div key={e.id} style={{ border: "1px solid var(--red)", borderRadius: 10, padding: 14, marginBottom: 12 }}>
              <div className="mono" style={{ color: "var(--red)", fontWeight: 600 }}>{e.error_type}</div>
              <div className="note" style={{ marginTop: 4 }}>{fmtTime(e.occurred_at)}</div>
              <pre className="json" style={{ marginTop: 8 }}>{e.message}</pre>
              {e.traceback && (
                <details>
                  <summary className="note" style={{ cursor: "pointer", marginTop: 8 }}>Show traceback</summary>
                  <pre className="json" style={{ marginTop: 8, maxHeight: 400 }}>{e.traceback}</pre>
                </details>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ---------------- Raw ----------------------------------------- */}
      {tab === "raw" && <JsonView data={run} />}
    </div>
  );
}

export default function RunDetailPage() {
  const { runId } = useParams();
  const [run, setRun] = useState<RunDetailT | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const navigate = useNavigate();

  // Initial REST load
  useEffect(() => {
    let alive = true;
    api.getRun(runId!).then((r) => alive && setRun(r)).catch((e) => alive && setErr(String(e)));
    return () => { alive = false; };
  }, [runId]);

  // Live WebSocket updates: mutate state as events arrive
  useEffect(() => {
    api_subscribe(runId!, (ev) => {
      setLive(true);
      setRun((prev) => {
        if (!prev) return prev;
        const apply = (p: RunDetailT): RunDetailT => {
          switch (ev.type) {
            case "llm_span":
              return { ...p, llm_requests: [...p.llm_requests.filter((s) => s.id !== ev.span_id), {
                id: ev.span_id, run_id: ev.run_id, seq: p.llm_requests.length + 1, trace_id: ev.trace_id,
                model: ev.model, provider: ev.provider, input_tokens: ev.input_tokens,
                output_tokens: ev.output_tokens, cost_usd: ev.cost_usd, duration_ms: ev.duration_ms,
                finish_reason: ev.finish_reason, request_messages: [], response_text: ev.response_text,
                invocation_params: {}, started_at: ev.started_at, finished_at: ev.finished_at,
              }] };
            case "tool_end":
              return { ...p, tool_calls: [...p.tool_calls.filter((t) => t.id !== ev.span_id), {
                id: ev.span_id, run_id: ev.run_id, trace_id: ev.trace_id, tool_name: ev.tool_name,
                input_data: null, output_data: ev.output_data, success: ev.success, error: ev.error,
                duration_ms: ev.duration_ms, started_at: ev.started_at, finished_at: ev.finished_at,
              }] };
            case "state_transition":
              return { ...p, state_transitions: [...p.state_transitions, {
                id: `${ev.seq}-${ev.step_type}`, run_id: ev.run_id, seq: ev.seq, step_type: ev.step_type,
                node_name: ev.node_name, state_snapshot: ev.state_snapshot, occurred_at: null,
              }] };
            case "memory_update":
              return { ...p, memory_updates: [...p.memory_updates, {
                id: `mem-${ev.seq}`, run_id: ev.run_id, seq: ev.seq, source: ev.source, namespace: null,
                memory_key: ev.memory_key, old_value: ev.old_value, new_value: ev.new_value, occurred_at: null,
              }] };
            case "error":
              return { ...p, errors: [...p.errors, {
                id: `err-${ev.trace_id ?? Date.now()}`, run_id: ev.run_id, trace_id: ev.trace_id,
                error_type: ev.error_type, message: ev.message, traceback: ev.traceback, occurred_at: null,
              }] };
            case "run_complete":
              return { ...p, status: ev.status as RunDetailT["status"], duration_ms: ev.duration_ms,
                total_input_tokens: ev.total_input_tokens, total_output_tokens: ev.total_output_tokens,
                total_cost_usd: ev.total_cost_usd, finished_at: ev.finished_at };
            default:
              return p;
          }
        };
        return apply(prev);
      });
    });
    return () => {
      _unsub?.();
      _unsub = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return (
    <div>
      <div className="note" style={{ marginBottom: 10 }}>
        <Link className="link" to="/runs">← All runs</Link>
        {live && <span style={{ marginLeft: 14 }}><span className="chip running"><span className="dot" />live stream</span></span>}
      </div>
      {err && <div className="card" style={{ borderColor: "var(--red)" }}>Could not load run: {err}</div>}
      {run && <Detail run={run} />}
    </div>
  );
}

/** Typed wrapper around the library subscription helper (async loader). */
let _unsub: (() => void) | null = null;
async function api_subscribe(runId: string, onEvent: (ev: TraceEvent) => void) {
  const { subscribeTrace } = await import("../lib/api");
  if (_unsub) _unsub();
  _unsub = subscribeTrace(`/api/ws/runs/${runId}`, onEvent);
}
