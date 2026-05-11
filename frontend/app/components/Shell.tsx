"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot, Home, MessageSquareText, Server } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type MCPServer } from "../lib/api";

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [apiOnline, setApiOnline] = useState(false);

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [health, status] = await Promise.all([api.health(), api.mcpStatus()]);
        if (!live) return;
        setApiOnline(health.status === "ok");
        setServers(status.active_servers || []);
      } catch {
        if (!live) return;
        setApiOnline(false);
        setServers([]);
      }
    }
    load();
    const timer = window.setInterval(load, 6000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);

  const online = servers.filter((server) => server.running && server.initialized).length;
  const total = servers.length;
  const mcpClass = total === 0 ? "bad" : online === total ? "good" : "warn";

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Bot size={20} /></div>
          <div>
            <h1>Agentic Framework</h1>
            <p>Builder console</p>
          </div>
        </div>

        <nav className="nav">
          <Link className={`nav-link ${pathname === "/" ? "active" : ""}`} href="/">
            <Home size={18} /> Home
          </Link>
          <Link className={`nav-link ${pathname === "/chat" ? "active" : ""}`} href="/chat">
            <MessageSquareText size={18} /> Chat
          </Link>
        </nav>

        <div className="sidebar-status">
          <div className="status-card">
            <div className="status-line">
              <span className={`dot ${apiOnline ? "good" : "bad"}`} />
              API {apiOnline ? "online" : "offline"}
            </div>
          </div>
          <div className="status-card">
            <div className="status-line">
              <Server size={16} />
              <span className={`dot ${mcpClass}`} />
              {total === 0 ? "No MCP status" : `${online}/${total} MCPs ready`}
            </div>
          </div>
        </div>
      </aside>
      <section className="content">{children}</section>
    </main>
  );
}
