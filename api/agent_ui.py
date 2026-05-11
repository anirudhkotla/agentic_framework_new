"""
api/agent_ui.py
---------------
Serves a fully self-contained chat UI for a single agent at:

  GET /agents/{agent_id}/ui        → HTML chat interface
  POST /agents/{agent_id}/chat     → send message, get response (JSON)
  GET /agents/{agent_id}/stream    → SSE stream for a message
  GET /agents/{agent_id}/info      → agent config + MCP status

This means any created agent is accessible at a stable URL like:
  http://localhost:8002/agents/agent-xxxxxxxx/ui

No Streamlit, no separate process — pure FastAPI + vanilla HTML/JS.
Share the URL with anyone on the same network and they can use the agent.
"""

from __future__ import annotations

import json
import uuid
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

logger = logging.getLogger(__name__)


def _chat_html(agent_id: str, agent_name: str, model: str, mcps: list[str]) -> str:
    """Generate the full HTML/CSS/JS chat page for a single agent."""
    mcp_badges = "".join(
        f'<span class="badge">{m}</span>' for m in mcps
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{agent_name} — Agent Chat</title>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e8eaf0; --muted: #8b8fa8; --accent: #639922;
    --accent-dim: rgba(99,153,34,0.15); --error: #e05252;
    --user-bg: #1e2433; --agent-bg: #161923;
    --radius: 12px; --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: var(--font);
          height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}

  /* Header */
  .header {{ background: var(--surface); border-bottom: 1px solid var(--border);
             padding: 14px 24px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
  .header-icon {{ font-size: 1.6rem; }}
  .header-info {{ flex: 1; }}
  .header-name {{ font-size: 1.1rem; font-weight: 600; }}
  .header-meta {{ font-size: 0.78rem; color: var(--muted); margin-top: 2px; }}
  .badge {{ background: var(--accent-dim); color: var(--accent); font-size: 11px;
            padding: 2px 8px; border-radius: 20px; margin-right: 4px; }}
  .status-dot {{ width: 8px; height: 8px; border-radius: 50%;
                 background: var(--accent); display: inline-block; margin-right: 6px; }}

  /* Messages area */
  .messages {{ flex: 1; overflow-y: auto; padding: 20px 24px; display: flex;
               flex-direction: column; gap: 16px; }}
  .msg {{ max-width: 82%; border-radius: var(--radius); padding: 12px 16px;
          line-height: 1.55; font-size: 0.92rem; }}
  .msg-user  {{ background: var(--user-bg); border: 1px solid var(--border);
                align-self: flex-end; border-bottom-right-radius: 4px; }}
  .msg-agent {{ background: var(--agent-bg); border: 1px solid var(--border);
                align-self: flex-start; border-bottom-left-radius: 4px; }}
  .msg-error {{ background: rgba(224,82,82,0.12); border: 1px solid rgba(224,82,82,0.3);
                align-self: flex-start; color: #e8a0a0; }}
  .msg pre  {{ background: #0d0f18; border: 1px solid var(--border); border-radius: 8px;
               padding: 10px 14px; overflow-x: auto; font-size: 0.85rem; margin-top: 8px; }}
  .msg code {{ background: #0d0f18; padding: 1px 5px; border-radius: 4px; font-size: 0.88em; }}
  .msg-meta {{ font-size: 0.72rem; color: var(--muted); margin-top: 8px; }}

  /* Tool calls */
  .tool-calls {{ margin-top: 10px; }}
  .tool-toggle {{ background: none; border: 1px solid var(--border); color: var(--muted);
                  font-size: 0.75rem; padding: 3px 10px; border-radius: 6px; cursor: pointer;
                  margin-bottom: 6px; }}
  .tool-toggle:hover {{ border-color: var(--accent); color: var(--accent); }}
  .tool-list {{ display: none; }}
  .tool-list.open {{ display: block; }}
  .tool-item {{ background: #0d0f18; border: 1px solid var(--border); border-radius: 8px;
                padding: 8px 12px; margin-bottom: 6px; font-size: 0.78rem; }}
  .tool-name {{ color: var(--accent); font-weight: 600; margin-bottom: 4px; }}
  .tool-result {{ color: var(--muted); max-height: 120px; overflow-y: auto; white-space: pre-wrap; }}
  .tool-error  {{ color: var(--error); }}

  /* Typing indicator */
  .typing {{ align-self: flex-start; padding: 12px 16px; background: var(--agent-bg);
             border: 1px solid var(--border); border-radius: var(--radius) var(--radius) var(--radius) 4px;
             display: none; }}
  .typing.show {{ display: flex; gap: 5px; align-items: center; }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--muted);
          animation: bounce 1.2s infinite; }}
  .dot:nth-child(2) {{ animation-delay: 0.2s; }}
  .dot:nth-child(3) {{ animation-delay: 0.4s; }}
  @keyframes bounce {{ 0%,60%,100%{{transform:translateY(0)}} 30%{{transform:translateY(-6px)}} }}

  /* Input area */
  .input-area {{ background: var(--surface); border-top: 1px solid var(--border);
                 padding: 16px 24px; display: flex; gap: 10px; align-items: flex-end; flex-shrink: 0; }}
  .input-wrap {{ flex: 1; background: var(--bg); border: 1px solid var(--border);
                 border-radius: var(--radius); overflow: hidden; }}
  #msg-input {{ width: 100%; background: none; border: none; outline: none; color: var(--text);
                font-family: var(--font); font-size: 0.92rem; padding: 12px 16px;
                resize: none; min-height: 46px; max-height: 160px; line-height: 1.5; }}
  .send-btn {{ background: var(--accent); border: none; color: #fff; font-size: 1.1rem;
               width: 44px; height: 44px; border-radius: 10px; cursor: pointer;
               display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
  .send-btn:hover {{ opacity: 0.85; }}
  .send-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .clear-btn {{ background: none; border: 1px solid var(--border); color: var(--muted);
                font-size: 0.78rem; padding: 8px 14px; border-radius: 8px; cursor: pointer; flex-shrink: 0; }}
  .clear-btn:hover {{ border-color: var(--error); color: var(--error); }}

  /* Scrollbar */
  ::-webkit-scrollbar {{ width: 5px; }} ::-webkit-scrollbar-track {{ background: transparent; }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

  /* Markdown-like formatting */
  .msg strong {{ font-weight: 600; }}
  .msg table {{ border-collapse: collapse; width: 100%; margin-top: 8px; font-size: 0.85rem; }}
  .msg th, .msg td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
  .msg th {{ background: #0d0f18; }}
  .msg ul, .msg ol {{ padding-left: 20px; margin-top: 6px; }}
  .msg li {{ margin-bottom: 3px; }}
</style>
</head>
<body>

<div class="header">
  <div class="header-icon">🤖</div>
  <div class="header-info">
    <div class="header-name">{agent_name}</div>
    <div class="header-meta">
      <span class="status-dot"></span>
      <code style="font-size:11px;color:#8b8fa8">{agent_id}</code>
      &nbsp;·&nbsp; {model}
      &nbsp;·&nbsp; {mcp_badges if mcp_badges else '<span style="color:#8b8fa8">base MCPs only</span>'}
    </div>
  </div>
  <button class="clear-btn" onclick="clearSession()">Clear memory</button>
</div>

<div class="messages" id="messages">
  <div class="msg msg-agent">
    👋 Hi! I'm <strong>{agent_name}</strong>. How can I help you today?
  </div>
</div>

<div class="typing" id="typing">
  <div class="dot"></div><div class="dot"></div><div class="dot"></div>
</div>

<div class="input-area">
  <div class="input-wrap">
    <textarea id="msg-input" placeholder="Message {agent_name}..."
              rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
  </div>
  <button class="send-btn" id="send-btn" onclick="sendMessage()" title="Send (Enter)">▶</button>
</div>

<script>
const AGENT_ID = "{agent_id}";
const API_BASE = window.location.origin;
let sessionId  = crypto.randomUUID();
let busy = false;

// ── Auto-resize textarea ──────────────────────────────────────────────────────
function autoResize(el) {{
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}}

// ── Enter to send, Shift+Enter for newline ────────────────────────────────────
function handleKey(e) {{
  if (e.key === "Enter" && !e.shiftKey) {{ e.preventDefault(); sendMessage(); }}
}}

// ── Scroll to bottom ──────────────────────────────────────────────────────────
function scrollDown() {{
  const el = document.getElementById("messages");
  el.scrollTop = el.scrollHeight;
}}

// ── Simple markdown renderer ──────────────────────────────────────────────────
function renderMarkdown(text) {{
  return text
    .replace(/```([\\s\\S]*?)```/g, (_,c) => `<pre><code>${{escHtml(c.trim())}}</code></pre>`)
    .replace(/`([^`]+)`/g, (_,c) => `<code>${{escHtml(c)}}</code>`)
    .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>")
    .replace(/\\*([^*]+)\\*/g, "<em>$1</em>")
    .replace(/^### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^## (.+)$/gm, "<h3>$1</h3>")
    .replace(/^# (.+)$/gm, "<h2>$1</h2>")
    .replace(/^\\- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\\/li>)/gs, "<ul>$1</ul>")
    .replace(/\\n\\n/g, "<br><br>")
    .replace(/\\n/g, "<br>");
}}
function escHtml(s) {{
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

// ── Add message to DOM ────────────────────────────────────────────────────────
function addMessage(role, content, toolCalls, meta) {{
  const msgs = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = `msg msg-${{role}}`;

  if (role === "error") {{
    div.className = "msg msg-error";
    div.innerHTML = `⚠️ ${{escHtml(content)}}`;
  }} else {{
    div.innerHTML = renderMarkdown(content);
  }}

  if (toolCalls && toolCalls.length > 0) {{
    const tc = document.createElement("div");
    tc.className = "tool-calls";
    const uid = "tc_" + Math.random().toString(36).slice(2);
    tc.innerHTML = `
      <button class="tool-toggle" onclick="toggleTools('${{uid}}')">
        🔧 ${{toolCalls.length}} tool call(s)
      </button>
      <div class="tool-list" id="${{uid}}">
        ${{toolCalls.map(t => `
          <div class="tool-item">
            <div class="tool-name">${{escHtml(t.server_id)}} / ${{escHtml(t.tool_name)}}</div>
            ${{t.error
              ? `<div class="tool-error">Error: ${{escHtml(t.error)}}</div>`
              : `<div class="tool-result">${{escHtml(JSON.stringify(t.result, null, 2) || "").slice(0,400)}}</div>`
            }}
          </div>`).join("")}}
      </div>`;
    div.appendChild(tc);
  }}

  if (meta) {{
    const m = document.createElement("div");
    m.className = "msg-meta";
    m.textContent = meta;
    div.appendChild(m);
  }}

  msgs.appendChild(div);
  scrollDown();
  return div;
}}

function toggleTools(uid) {{
  const el = document.getElementById(uid);
  el.classList.toggle("open");
}}

// ── Send message ──────────────────────────────────────────────────────────────
async function sendMessage() {{
  if (busy) return;
  const input = document.getElementById("msg-input");
  const text  = input.value.trim();
  if (!text) return;

  input.value = "";
  input.style.height = "auto";
  addMessage("user", text, null, null);

  busy = true;
  document.getElementById("send-btn").disabled = true;
  document.getElementById("typing").classList.add("show");
  scrollDown();

  try {{
    const resp = await fetch(`${{API_BASE}}/agents/${{AGENT_ID}}/chat`, {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{
        session_id: sessionId,
        message: text,
      }}),
    }});
    const data = await resp.json();

    document.getElementById("typing").classList.remove("show");

    if (data.success && data.response) {{
      const r = data.response;
      const meta = `Iter: ${{r.iterations_used}} · ${{r.duration_ms}}ms · ${{r.status}}`;
      addMessage("agent", r.message, r.tool_calls, meta);
    }} else {{
      addMessage("error", data.error || "Unknown error", null, null);
    }}
  }} catch(e) {{
    document.getElementById("typing").classList.remove("show");
    addMessage("error", "API connection failed: " + e.message, null, null);
  }} finally {{
    busy = false;
    document.getElementById("send-btn").disabled = false;
    input.focus();
  }}
}}

// ── Clear session ─────────────────────────────────────────────────────────────
async function clearSession() {{
  try {{
    await fetch(`${{API_BASE}}/api/v1/agents/${{AGENT_ID}}/memory/${{sessionId}}`, {{method:"DELETE"}});
  }} catch(e) {{}}
  sessionId = crypto.randomUUID();
  const msgs = document.getElementById("messages");
  msgs.innerHTML = `<div class="msg msg-agent">🧹 Memory cleared. Starting fresh!</div>`;
  scrollDown();
}}

// ── Focus input on load ───────────────────────────────────────────────────────
window.onload = () => document.getElementById("msg-input").focus();
</script>
</body>
</html>"""


def register_agent_ui_routes(app, agent_manager):
    """
    Register per-agent UI routes on the FastAPI app.
    Call this from create_app() after all other routes are registered.
    """

    @app.get("/agents/{agent_id}/ui", response_class=HTMLResponse, tags=["agent-ui"])
    async def agent_ui(agent_id: str):
        """
        Serve the chat UI for a specific agent.
        URL: http://localhost:8002/agents/{agent_id}/ui
        """
        config = agent_manager.get(agent_id)
        if not config:
            raise HTTPException(
                404,
                f"Agent '{agent_id}' not found. "
                "Create it first via POST /api/v1/agents or the Streamlit UI."
            )
        html = _chat_html(
            agent_id=config.agent_id,
            agent_name=config.name,
            model=config.model_name,
            mcps=config.selected_mcp_ids,
        )
        return HTMLResponse(content=html)

    @app.post("/agents/{agent_id}/chat", tags=["agent-ui"])
    async def agent_chat(agent_id: str, body: dict):
        """
        Send a message to an agent from the UI.
        Body: {"session_id": "...", "message": "..."}
        """
        config = agent_manager.get(agent_id)
        if not config:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        session_id = body.get("session_id") or str(uuid.uuid4())
        message    = body.get("message", "").strip()
        if not message:
            raise HTTPException(400, "message is required")

        from core.models import RunAgentResponse
        resp = await agent_manager.run(
            agent_id=agent_id,
            session_id=session_id,
            message=message,
        )
        return {"success": resp.status.value != "error", "response": resp.model_dump()}

    @app.get("/agents/{agent_id}/stream", tags=["agent-ui"])
    async def agent_stream(agent_id: str, session_id: str, message: str):
        """
        Stream a response for an agent via SSE.
        Usage: GET /agents/{agent_id}/stream?session_id=...&message=...
        """
        config = agent_manager.get(agent_id)
        if not config:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        async def _gen() -> AsyncIterator[str]:
            async for tok in agent_manager.stream(agent_id, session_id, message):
                yield f"data: {tok}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/agents/{agent_id}/info", tags=["agent-ui"])
    async def agent_info(agent_id: str, request: Request):
        """
        Get agent config + MCP status + UI URL.
        Useful for embedding or linking to the agent from other systems.
        """
        config = agent_manager.get(agent_id)
        if not config:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        base_url = str(request.base_url).rstrip("/")
        return {
            "agent_id": config.agent_id,
            "name": config.name,
            "model": config.model_name,
            "usecase_context": config.usecase_context,
            "selected_mcp_ids": config.selected_mcp_ids,
            "selected_plugin_ids": config.selected_plugin_ids,
            "ui_url": f"{base_url}/agents/{agent_id}/ui",
            "chat_url": f"{base_url}/agents/{agent_id}/chat",
            "stream_url": f"{base_url}/agents/{agent_id}/stream",
        }

    @app.get("/agents", tags=["agent-ui"])
    async def agents_index(request: Request):
        """
        HTML index page listing all running agents with links to their UIs.
        URL: http://localhost:8002/agents
        """
        agents = agent_manager.list_agents()
        base_url = str(request.base_url).rstrip("/")

        if not agents:
            body = "<p style='color:#8b8fa8'>No agents running. Create one via the Streamlit UI.</p>"
        else:
            rows = "".join(
                f"""<a href="{base_url}/agents/{a['agent_id']}/ui" class="card">
                      <div class="card-name">{a['name']}</div>
                      <div class="card-meta">{a['agent_id']} · {a['model']}</div>
                      <div class="card-usecase">{a['usecase'][:80]}...</div>
                    </a>"""
                for a in agents
            )
            body = f'<div class="grid">{rows}</div>'

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Agentic Framework — Agents</title>
<style>
  body {{ background:#0f1117; color:#e8eaf0; font-family:-apple-system,sans-serif;
          padding:40px 32px; }}
  h1 {{ font-size:1.6rem; margin-bottom:6px; }}
  p.sub {{ color:#8b8fa8; margin-bottom:32px; font-size:0.9rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; }}
  .card {{ background:#1a1d27; border:1px solid #2a2d3a; border-radius:12px;
           padding:18px; text-decoration:none; color:inherit; display:block;
           transition:border-color 0.2s; }}
  .card:hover {{ border-color:#639922; }}
  .card-name {{ font-size:1rem; font-weight:600; margin-bottom:4px; }}
  .card-meta {{ font-size:0.75rem; color:#8b8fa8; margin-bottom:8px; font-family:monospace; }}
  .card-usecase {{ font-size:0.82rem; color:#aaa; line-height:1.4; }}
</style>
</head>
<body>
<h1>🤖 Agentic Framework</h1>
<p class="sub">{len(agents)} agent(s) running · <a href="/api/docs" style="color:#639922">API docs</a></p>
{body}
</body>
</html>"""
        return HTMLResponse(content=html)
