import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Agent, type TraceEvent } from "../lib/api";
import { fmtMs, fmtTime, fmtTokens, fmtUsd, prettyJson } from "../lib/format";

interface ChatMsg {
  role: "user" | "assistant" | "system";
  content: string;
}

interface LiveEvent {
  ts: string;
  kind: string;
  label: string;
  detail: unknown;
}

export default function Playground() {
  const [params] = useSearchParams();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState(params.get("agent_id") ?? "");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMsg[]>([
    { role: "system", content: "Pick an agent on the right, type a message, and watch the trace arrive live over WebSockets." },
  ]);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const eventsEnd = useRef<HTMLDivElement>(null);

  useEffect(() => { api.listAgents().then(setAgents); }, []);
  useEffect(() => { eventsEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [events]);

  useEffect(() => {
    if (!lastRunId || !running) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/api/ws/runs/${lastRunId}`);
    ws.onmessage = (ev) => {
      try {
        const e = JSON.parse(ev.data) as TraceEvent;
        if (e.type === "ping") return;
        const mk = (kind: string, label: string, detail: unknown): LiveEvent => ({
          ts: new Date().toLocaleTimeString(), kind, label, detail,
        });
        let entry: LiveEvent | null = null;
        if (e.type === "run_start") entry = mk("run", "Run started — connecting to trace…", e);
        if (e.type === "llm_span") entry = mk("llm", `LLM · ${e.model} · ${fmtTokens(e.input_tokens + e.output_tokens)} tok · ${fmtUsd(e.cost_usd)}`, e);
        else if (e.type === "tool_start") entry = mk("tool", `Tool call · ${e.tool_name}`, e);
        else if (e.type === "tool_end") entry = mk("tool", `Tool done · ${e.tool_name} (${e.success ? "ok" : "failed"})`, e);
        else if (e.type === "state_transition") entry = mk("state", `State · ${e.step_type} · ${e.node_name ?? "checkpoint"}`, e);
        else if (e.type === "memory_update") entry = mk("memory", `Memory · ${e.memory_key} (${e.source})`, e);
        else if (e.type === "error") entry = mk("error", `Error · ${e.error_type}`, e);
        else if (e.type === "run_complete") {
          entry = mk("run", `Run complete · ${e.status} · ${fmtMs(e.duration_ms)} · ${fmtUsd(e.total_cost_usd)}`, e);
          setRunning(false);
          setMessages((m) => [...m, {
            role: "system",
            content: `Run finished (${e.status}). Duration ${fmtMs(e.duration_ms)}, tokens ${fmtTokens(e.total_input_tokens + e.total_output_tokens)}, cost ${fmtUsd(e.total_cost_usd)}. Open the run in the Runs view for the full trace.`,
          }]);
        }
        if (entry) setEvents((evs) => [...evs, entry]);
      } catch { /* ignore */ }
    };
    ws.onclose = () => setRunning(false);
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastRunId]);

  async function send() {
    if (!input.trim() || !agentId || running) return;
    setErr(null);
    const userMsg = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content: userMsg }]);
    setEvents([]);
    try {
      const { run_id } = await api.invokeAgent(agentId, userMsg, userMsg.slice(0, 60));
      setLastRunId(run_id);
      setRunning(true);
    } catch (e) {
      setErr(String(e));
      setMessages((m) => [...m, { role: "system", content: `Invoke failed: ${e}` }]);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="h1" style={{ margin: 0 }}>Playground</h1>
        <div className="note">
          Invoke an agent and watch every LLM call, tool call, state transition and memory update arrive live over
          WebSockets.
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, alignItems: "start" }}>
        {/* Chat */}
        <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: 480 }}>
          <div style={{ display: "flex", gap: 10, marginBottom: 12 }}>
            <select className="input" style={{ flex: 1 }} value={agentId}
              onChange={(e) => setAgentId(e.target.value)}>
              <option value="">Select agent…</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.model})</option>)}
            </select>
          </div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 10, marginBottom: 12, overflowY: "auto", maxHeight: 420 }}>
            {messages.map((m, i) => (
              <div key={i} style={{
                alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                background: m.role === "user" ? "var(--accent-dim)" : m.role === "system" ? "var(--bg-raised)" : "var(--bg-raised)",
                border: m.role === "system" ? "1px dashed var(--border)" : "1px solid var(--border)",
                borderRadius: 10, padding: "8px 12px", maxWidth: "85%", fontSize: 14,
              }}>
                {m.content}
              </div>
            ))}
          </div>
          {err && <div style={{ color: "var(--red)", fontSize: 13, marginBottom: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8 }}>
            <input className="input" placeholder={agentId ? "Ask the agent…" : "Select an agent first"}
              value={input} disabled={!agentId || running}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()} />
            <button className="btn" disabled={!agentId || running || !input.trim()} onClick={send}>
              {running ? "Running…" : "Invoke"}
            </button>
          </div>
        </div>

        {/* Live trace feed */}
        <div className="card" style={{ minHeight: 480, maxHeight: 620, overflowY: "auto" }}>
          <div className="section-title">
            Live trace
            {running && <span className="chip running" style={{ marginLeft: 10 }}><span className="dot" />streaming</span>}
          </div>
          {events.length === 0 ? (
            <div className="empty">Events will appear here as the agent executes.</div>
          ) : events.map((e, i) => (
            <details key={i} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "6px 10px", marginBottom: 6 }}>
              <summary style={{ fontSize: 12.5, cursor: "pointer", listStyle: "none" }}>
                <span className="mono" style={{ color: "var(--text-dim)", marginRight: 8 }}>{e.ts}</span>
                <b>{e.label}</b>
              </summary>
              <pre className="json" style={{ marginTop: 6, maxHeight: 180 }}>{prettyJson(e.detail)}</pre>
            </details>
          ))}
          <div ref={eventsEnd} />
        </div>
      </div>
    </div>
  );
}
