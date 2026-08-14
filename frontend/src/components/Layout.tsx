import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { path: "/", label: "Dashboard", icon: "◫" },
  { path: "/runs", label: "Runs", icon: "▶" },
  { path: "/agents", label: "Agents", icon: "⚙" },
  { path: "/playground", label: "Playground", icon: "⚡" },
  { path: "/docs", label: "API Docs", icon: "ⓘ" },
];

export default function Layout() {
  return (
    <div className="page-shell">
      <nav className="sidebar">
        <div className="brand">
          <span style={{ color: "var(--accent)", fontSize: 18 }}>◈</span>
          AgentScope
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === "/"}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            <span>{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
        <div style={{ marginTop: "auto", paddingTop: 16, fontSize: 11, color: "var(--text-dim)" }}>
          LangGraph observability
          <br />
          open-source · v1.0
        </div>
      </nav>
      <main style={{ padding: "24px 28px", maxWidth: 1400, width: "100%" }}>
        <Outlet />
      </main>
    </div>
  );
}
