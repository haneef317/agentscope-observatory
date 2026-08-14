import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Agent } from "../lib/api";
import { fmtRel } from "../lib/format";

const AGENT_TYPES = [
  { value: "react_agent", label: "ReAct agent" },
  { value: "supervisor", label: "Supervisor (multi-agent)" },
  { value: "custom", label: "Custom graph" },
];

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", agent_type: "react_agent", model: "gpt-4o-mini" });
  const [err, setErr] = useState<string | null>(null);
  const navigate = useNavigate();

  const reload = () => api.listAgents().then(setAgents).catch((e) => setErr(String(e)));
  useEffect(() => { reload(); }, []);

  async function createAgent(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.createAgent({
        name: form.name,
        description: form.description || null,
        agent_type: form.agent_type,
        model: form.model,
        tools: [{ name: "web_search", description: "Search the web" }],
      });
      setShowForm(false);
      setForm({ name: "", description: "", agent_type: "react_agent", model: "gpt-4o-mini" });
      reload();
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="h1" style={{ margin: 0 }}>Agents</h1>
        <button className="btn" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "+ Register agent"}
        </button>
      </div>

      {err && <div className="card" style={{ borderColor: "var(--red)", marginBottom: 12 }}>{err}</div>}

      {showForm && (
        <div className="card" style={{ marginBottom: 14, maxWidth: 640 }}>
          <form onSubmit={createAgent} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="section-title" style={{ margin: 0 }}>New agent</div>
            <input className="input" placeholder="Agent name (e.g. Customer Support Bot)" required
              value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className="input" placeholder="Description (optional)"
              value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            <select className="input" value={form.agent_type}
              onChange={(e) => setForm({ ...form, agent_type: e.target.value })}>
              {AGENT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
            <input className="input" placeholder="Model id (e.g. gpt-4o-mini, claude-sonnet-4-5)"
              value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
            <div className="note">
              If no <code>OPENAI_API_KEY</code> is configured on the backend, agents run in <b>simulator mode</b> —
              full realistic traces without real LLM calls.
            </div>
            <button className="btn" type="submit">Register</button>
          </form>
        </div>
      )}

      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <table className="data">
          <thead>
            <tr><th>Agent</th><th>Type</th><th>Model</th><th>Tools</th><th>Created</th><th></th></tr>
          </thead>
          <tbody>
            {agents.length === 0 && <tr><td colSpan={6} className="empty">No agents registered yet.</td></tr>}
            {agents.map((a) => (
              <tr key={a.id}>
                <td>
                  <div style={{ fontWeight: 600 }}>{a.name}</div>
                  <div className="note" style={{ maxWidth: 360 }}>{a.description ?? "—"}</div>
                </td>
                <td><span className="chip running">{a.agent_type}</span></td>
                <td className="mono">{a.model}</td>
                <td>{a.tools.length}</td>
                <td className="note">{fmtRel(a.created_at)}</td>
                <td style={{ display: "flex", gap: 6 }}>
                  <button className="btn secondary" style={{ padding: "4px 12px", fontSize: 12 }}
                    onClick={() => navigate(`/playground?agent_id=${a.id}`)}>
                    ▶ Run
                  </button>
                  {!a.is_demo && (
                    <button className="btn danger" style={{ padding: "4px 10px", fontSize: 12 }}
                      onClick={() => {
                        if (confirm(`Delete agent ${a.name}? Its runs will be deleted too.`)) {
                          api.deleteAgent(a.id).then(reload).catch((e) => setErr(String(e)));
                        }
                      }}>
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
