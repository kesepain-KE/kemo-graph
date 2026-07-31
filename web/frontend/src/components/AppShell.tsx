import {
  Activity,
  Database,
  FileText,
  Network,
  Search,
  Settings2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "../api/api";

const navItems = [
  { to: "/documents", label: "文档管理", icon: FileText },
  { to: "/graph", label: "知识图谱", icon: Network },
  { to: "/search", label: "知识检索", icon: Search },
  { to: "/settings", label: "系统配置", icon: Settings2 },
  { to: "/status", label: "运行状态", icon: Activity },
];

const pageMeta: Record<string, { title: string; eyebrow: string }> = {
  "/documents": { title: "文档管理", eyebrow: "Sources / Ingest" },
  "/graph": { title: "知识图谱", eyebrow: "Explore / Visualize" },
  "/search": { title: "知识检索", eyebrow: "Graph / RAG / Hybrid" },
  "/settings": { title: "系统配置", eyebrow: "Runtime / Providers" },
  "/status": { title: "运行状态", eyebrow: "Health / Metrics" },
};

function BrandMark() {
  return (
    <div className="brand-mark">
      <img src="/kemo-graph-logo.png" alt="kemo-graph" />
    </div>
  );
}

export function AppShell() {
  const location = useLocation();
  const meta = pageMeta[location.pathname] ?? pageMeta["/graph"];
  const [serviceState, setServiceState] = useState<"online" | "offline" | "checking">(
    "checking",
  );

  useEffect(() => {
    let active = true;
    api
      .status()
      .then(() => active && setServiceState("online"))
      .catch(() => active && setServiceState("offline"));
    return () => {
      active = false;
    };
  }, [location.pathname]);

  return (
    <div className="app-shell">
      <aside className="nav-rail">
        <NavLink className="brand" to="/graph" aria-label="kemo-graph 首页">
          <BrandMark />
          <span className="brand-name">
            <strong>kemo-graph</strong>
            <small>Knowledge Base</small>
          </span>
        </NavLink>

        <nav className="nav-links" aria-label="主导航">
          <span className="nav-heading">Navigation</span>
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`}
              aria-label={label}
              title={label}
            >
              <Icon size={20} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

      </aside>

      <div className={`app-workspace ${location.pathname === "/settings" ? "is-settings" : ""}`}>
        <header className="topbar">
          <div>
            <p className="eyebrow">{meta.eyebrow}</p>
            <h1>{meta.title}</h1>
          </div>
          <div className="topbar-actions">
            <div className={`service-pill is-${serviceState}`}>
              <span />
              {serviceState === "online"
                ? "本地服务在线"
                : serviceState === "offline"
                  ? "服务未连接"
                  : "正在检查"}
            </div>
            <div className="database-pill">
              <Database size={15} />
              <span>Local Knowledge Base</span>
            </div>
          </div>
        </header>

        <main className="page-stage">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
