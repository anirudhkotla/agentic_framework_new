"""
streamlit_app.py
----------------
Two-page Agentic Framework UI.

Page 1 — Home (agent picker):
  - Grid of defined agent cards (Coding, Content Writer, Marketing, HR, Data Analyst, DevOps)
  - Click any card → instantiate modal (usecase prompt + extra MCPs/plugins)
  - Plus button (top right) → custom agent builder (full form)

Page 2 — Chat:
  - Searchable agent dropdown
  - Chat window with tool call inspector
  - MCP status sidebar
"""

from __future__ import annotations

import os
import uuid
import requests
import streamlit as st
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

API_PORT = os.getenv("API_PORT", "8002")
API = f"http://localhost:{API_PORT}/api/v1"

st.set_page_config(
    page_title="Agentic Framework",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* Agent card */
div[data-testid="column"] .agent-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 14px;
    padding: 1.4rem;
    height: 100%;
    cursor: pointer;
    transition: border-color 0.2s;
}
div[data-testid="column"] .agent-card:hover {
    border-color: rgba(99,153,34,0.6);
}
.agent-icon  { font-size: 2.4rem; margin-bottom: 0.4rem; }
.agent-name  { font-size: 1.05rem; font-weight: 600; margin-bottom: 0.25rem; }
.agent-tag   { font-size: 0.8rem; color: #aaa; margin-bottom: 0.7rem; }
.agent-cap   { font-size: 0.78rem; color: #999; }
.pill {
    display: inline-block; font-size: 11px; padding: 2px 9px;
    border-radius: 20px; margin: 2px;
    background: rgba(99,153,34,0.15); color: #7cc144;
}
</style>
""", unsafe_allow_html=True)

# ─── Session defaults ─────────────────────────────────────────────────────────

for k, v in {
    "page": "home",
    "session_id": str(uuid.uuid4()),
    "messages": [],
    "active_agent_id": None,
    "active_agent_name": None,
    "show_custom_builder": False,
    "instantiate_template": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── API helpers ──────────────────────────────────────────────────────────────

def api_get(path, timeout=3):
    try:
        return requests.get(f"{API}{path}", timeout=timeout).json()
    except Exception:
        return None

def api_post(path, body, timeout=60):
    try:
        return requests.post(f"{API}{path}", json=body, timeout=timeout).json()
    except Exception:
        return None

def api_delete(path, timeout=5):
    try:
        return requests.delete(f"{API}{path}", timeout=timeout).json()
    except Exception:
        return None

def get_registry():
    r = api_get("/mcp/registry")
    return r if r else {"base": [], "selectable": [], "builtin_skills": []}

def get_agents():
    r = api_get("/agents")
    return r.get("agents", []) if r else []

def get_mcp_status():
    r = api_get("/mcp/status", timeout=2)
    return r.get("active_servers", []) if r else []

def get_defined_agents():
    r = api_get("/defined-agents")
    return r.get("defined_agents", []) if r else []

def get_plugins():
    r = api_get("/plugins")
    return r.get("plugins", []) if r else []

# ─── Top nav ──────────────────────────────────────────────────────────────────

nav_left, nav_center, nav_right = st.columns([2, 4, 2])

with nav_left:
    st.markdown("### 🤖 Agentic Framework")

with nav_center:
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("🏠  Home", use_container_width=True,
                     type="primary" if st.session_state.page == "home" else "secondary"):
            st.session_state.page = "home"
            st.session_state.show_custom_builder = False
            st.rerun()
    with c2:
        if st.button("💬  Chat", use_container_width=True,
                     type="primary" if st.session_state.page == "chat" else "secondary"):
            st.session_state.page = "chat"
            st.rerun()
    with c3:
        agents = get_agents()
        st.caption(f"**{len(agents)}** agent(s) ready")

with nav_right:
    mcp_servers = get_mcp_status()
    online = sum(1 for s in mcp_servers if s.get("running") and s.get("initialized"))
    total  = len(mcp_servers)
    if total == 0:
        st.caption("⚫ API offline")
    elif online == total:
        st.caption(f"🟢 {online}/{total} MCPs ready")
    else:
        st.caption(f"🟡 {online}/{total} MCPs ready")

st.divider()

# ═════════════════════════════════════════════════════════════════════════════
# PAGE 1 — HOME (Agent Picker)
# ═════════════════════════════════════════════════════════════════════════════

if st.session_state.page == "home":

    # ── Custom builder overlay ─────────────────────────────────────────────────
    if st.session_state.show_custom_builder:

        col_back, col_title = st.columns([1, 8])
        with col_back:
            if st.button("← Back"):
                st.session_state.show_custom_builder = False
                st.rerun()
        with col_title:
            st.subheader("Create a custom agent")

        reg = get_registry()
        selectable = reg.get("selectable", [])
        all_plugins = get_plugins()

        CATEGORY_ICONS = {
            "web": "🌐", "dev": "💻", "database": "🗄️",
            "productivity": "📋", "business": "💼", "devops": "⚙️", "ai_ml": "🧠",
        }
        PLUGIN_TYPE_ICONS = {"mcp": "🔌", "http_tool": "🌐", "python_skill": "🐍", "skill_md": "📝"}
        SOURCE_ICONS = {"community": "🏪", "user": "👤"}

        cats: dict[str, list] = {}
        for s in selectable:
            cats.setdefault(s.get("category", "other"), []).append(s)

        mcp_option_map: dict[str, str] = {}
        for cat, servers in sorted(cats.items()):
            icon = CATEGORY_ICONS.get(cat, "🔧")
            for srv in servers:
                label = f"{icon} {cat.upper()} — {srv['name']}"
                mcp_option_map[label] = srv["id"]

        plugin_option_map: dict[str, str] = {}
        for p in all_plugins:
            label = f"{PLUGIN_TYPE_ICONS.get(p['type'],'🔧')} {p['type'].upper()} — {p['name']} {SOURCE_ICONS.get(p['source'],'')}"
            plugin_option_map[label] = p["id"]

        left, right = st.columns([3, 2])
        with left:
            st.markdown("#### Identity")
            agent_name    = st.text_input("Agent name", placeholder="e.g. My Research Assistant")
            usecase       = st.text_area("Use-case context", height=120,
                                         placeholder="Describe what this agent does...")

            st.markdown("#### Model")
            MODELS = {
                "mistral-large-latest": "Mistral Large — Best quality",
                "mistral-small-latest": "Mistral Small — Fast & cheap",
                "codestral-latest":     "Codestral — Best for code",
                "open-mistral-nemo":    "Mistral Nemo — Lightweight",
            }
            model_labels  = list(MODELS.keys())
            model_display = [f"{k}  ·  {v}" for k, v in MODELS.items()]
            default_model = os.getenv("DEFAULT_MODEL", "mistral-large-latest")
            default_idx   = model_labels.index(default_model) if default_model in model_labels else 0
            sel_model_disp = st.selectbox("Model", model_display, index=default_idx, label_visibility="collapsed")
            sel_model = model_labels[model_display.index(sel_model_disp)]

            st.markdown("#### MCP Servers")
            st.caption("🔍 Type to search — all options stay visible")
            chosen_mcp_labels = st.multiselect("MCPs", list(mcp_option_map.keys()), default=[],
                                                placeholder="Search: 'github', 'slack', 'database'...",
                                                label_visibility="collapsed")
            chosen_mcp_ids = [mcp_option_map[l] for l in chosen_mcp_labels]

            st.markdown("#### Plugins")
            chosen_plugin_labels = st.multiselect("Plugins", list(plugin_option_map.keys()), default=[],
                                                   placeholder="Search: 'search', 'notion', 'usko'...",
                                                   label_visibility="collapsed")
            chosen_plugin_ids = [plugin_option_map[l] for l in chosen_plugin_labels]

            # Plugin upload
            uploaded = st.file_uploader("Upload .plugin.json", type=["json"], label_visibility="collapsed")
            if uploaded:
                import json as _j
                raw_bytes = uploaded.read()
                try:
                    _j.loads(raw_bytes)
                    resp = requests.post(
                        f"{API}/plugins/upload",
                        files={"file": (uploaded.name, raw_bytes, "application/json")},
                        timeout=10,
                    ).json()
                    if resp.get("success"):
                        st.success(f"✅ {resp['message']}")
                        st.rerun()
                    else:
                        st.error(resp.get("detail") or "Upload failed")
                except Exception as e:
                    st.error(f"Error: {e}")

        with right:
            st.markdown("#### Base layer (always active)")
            status_map = {s["id"]: s for s in get_mcp_status()}
            for srv in reg.get("base", []):
                s_info = status_map.get(srv["id"], {})
                icon = "🟢" if s_info.get("initialized") else ("🟡" if s_info.get("running") else "🔴")
                tools = s_info.get("tools", [])
                st.caption(f"{icon} **{srv['name']}**" + (f" ({len(tools)} tools)" if tools else ""))

            st.divider()
            st.markdown("#### Built-in skills")
            for sk in reg.get("builtin_skills", []):
                st.caption(f"⚡ **{sk['name']}**")

        st.divider()
        create_col, info_col = st.columns([1, 3])
        with create_col:
            create_clicked = st.button("🚀 Create agent", type="primary", use_container_width=True)
        with info_col:
            if agent_name.strip() and usecase.strip():
                st.success(f"Ready to create **{agent_name}** with `{sel_model}`")
            else:
                st.info("Fill in name and use-case to continue")

        if create_clicked:
            if not agent_name.strip() or not usecase.strip():
                st.error("Name and use-case are required.")
            else:
                with st.spinner("Creating agent..."):
                    r = api_post("/agents", {
                        "name": agent_name.strip(),
                        "usecase_context": usecase.strip(),
                        "selected_mcp_ids": chosen_mcp_ids,
                        "selected_plugin_ids": chosen_plugin_ids,
                        "model_name": sel_model,
                    })
                if r and r.get("success"):
                    st.session_state.active_agent_id   = r["agent_id"]
                    st.session_state.active_agent_name = agent_name.strip()
                    st.session_state.messages          = []
                    st.session_state.session_id        = str(uuid.uuid4())
                    st.success(f"✅ Created: `{r['agent_id']}`")
                    ui_url = f"http://localhost:{API_PORT}/agents/{r['agent_id']}/ui"
                    st.info(f"🌐 Direct URL: [{ui_url}]({ui_url})")
                    if r.get("agent_folder"):
                        st.caption(f"📁 `{r['agent_folder']}`")
                    st.balloons()
                    if st.button("💬 Go to Chat", type="primary"):
                        st.session_state.page = "chat"
                        st.session_state.show_custom_builder = False
                        st.rerun()
                else:
                    st.error(r.get("error") if r else "API unreachable")

    # ── Main home — defined agent cards ───────────────────────────────────────
    else:
        header_left, header_right = st.columns([6, 2])
        with header_left:
            st.subheader("Choose an agent to get started")
            st.caption("Pick a pre-built agent or create your own from scratch")
        with header_right:
            if st.button("➕  Custom agent", type="primary", use_container_width=True):
                st.session_state.show_custom_builder = True
                st.rerun()

        defined = get_defined_agents()

        if not defined:
            st.warning("No defined agents found. Check that `defined_agents/*.json` files exist.")
        else:
            # Render cards in rows of 3
            cols_per_row = 3
            for row_start in range(0, len(defined), cols_per_row):
                row = defined[row_start: row_start + cols_per_row]
                cols = st.columns(cols_per_row)
                for col, agent in zip(cols, row):
                    with col:
                        with st.container(border=True):
                            st.markdown(
                                f"<div class='agent-icon'>{agent['icon']}</div>"
                                f"<div class='agent-name'>{agent['name']}</div>"
                                f"<div class='agent-tag'>{agent['tagline']}</div>",
                                unsafe_allow_html=True,
                            )
                            for cap in agent.get("capabilities", [])[:3]:
                                st.caption(f"• {cap}")

                            # Suggested MCPs as pills
                            suggested = agent.get("suggested_mcps", [])
                            if suggested:
                                pills = " ".join(f"<span class='pill'>{m}</span>" for m in suggested[:4])
                                st.markdown(pills, unsafe_allow_html=True)

                            st.markdown("")
                            if st.button(
                                f"Use {agent['name']}",
                                key=f"pick_{agent['id']}",
                                use_container_width=True,
                                type="secondary",
                            ):
                                st.session_state.instantiate_template = agent
                                st.rerun()

        # ── Instantiate modal ──────────────────────────────────────────────────
        if st.session_state.instantiate_template:
            tmpl = st.session_state.instantiate_template

            st.divider()
            close_col, title_col = st.columns([1, 8])
            with close_col:
                if st.button("✕ Cancel"):
                    st.session_state.instantiate_template = None
                    st.rerun()
            with title_col:
                st.subheader(f"{tmpl['icon']} Configure {tmpl['name']}")

            left_m, right_m = st.columns([3, 2])

            reg        = get_registry()
            all_plugins = get_plugins()
            selectable  = reg.get("selectable", [])

            CATEGORY_ICONS = {
                "web": "🌐", "dev": "💻", "database": "🗄️",
                "productivity": "📋", "business": "💼", "devops": "⚙️", "ai_ml": "🧠",
            }
            PLUGIN_TYPE_ICONS = {"mcp": "🔌", "http_tool": "🌐", "python_skill": "🐍", "skill_md": "📝"}
            SOURCE_ICONS = {"community": "🏪", "user": "👤"}

            mcp_opt: dict[str, str] = {}
            for s in selectable:
                cat = s.get("category", "other")
                icon = CATEGORY_ICONS.get(cat, "🔧")
                mcp_opt[f"{icon} {cat.upper()} — {s['name']}"] = s["id"]

            plugin_opt: dict[str, str] = {}
            for p in all_plugins:
                label = f"{PLUGIN_TYPE_ICONS.get(p['type'],'🔧')} {p['type'].upper()} — {p['name']} {SOURCE_ICONS.get(p['source'],'')}"
                plugin_opt[label] = p["id"]

            # Pre-select suggested MCPs as defaults
            default_mcp_labels = [
                lbl for lbl, sid in mcp_opt.items()
                if sid in tmpl.get("suggested_mcps", [])
            ]
            default_plugin_labels = [
                lbl for lbl, pid in plugin_opt.items()
                if pid in tmpl.get("default_plugins", [])
            ]

            with left_m:
                st.markdown("**Use-case prompt**")
                st.caption("Pre-filled with the agent's default — customise for your needs")
                user_usecase = st.text_area(
                    "Usecase",
                    value=tmpl["default_usecase"],
                    height=150,
                    label_visibility="collapsed",
                )

                st.markdown("**Add extra MCP servers** (optional)")
                st.caption(f"🔍 Default MCPs pre-selected. Type to search for more.")
                extra_mcp_labels = st.multiselect(
                    "Extra MCPs",
                    options=list(mcp_opt.keys()),
                    default=default_mcp_labels,
                    placeholder="Search to add more...",
                    label_visibility="collapsed",
                )
                extra_mcp_ids = [mcp_opt[l] for l in extra_mcp_labels]

                st.markdown("**Add plugins** (optional)")
                extra_plugin_labels = st.multiselect(
                    "Plugins",
                    options=list(plugin_opt.keys()),
                    default=default_plugin_labels,
                    placeholder="Search plugins...",
                    label_visibility="collapsed",
                )
                extra_plugin_ids = [plugin_opt[l] for l in extra_plugin_labels]

            with right_m:
                st.markdown("**What this agent can do**")
                for cap in tmpl.get("capabilities", []):
                    st.caption(f"✓ {cap}")

                st.divider()
                st.markdown("**Example prompts**")
                for ex in tmpl.get("example_prompts", [])[:3]:
                    st.caption(f"› *{ex}*")

            st.markdown("")
            launch_col, _ = st.columns([2, 4])
            with launch_col:
                if st.button(
                    f"🚀 Launch {tmpl['name']}",
                    type="primary",
                    use_container_width=True,
                ):
                    with st.spinner(f"Creating {tmpl['name']} instance..."):
                        r = requests.post(
                            f"{API}/defined-agents/{tmpl['id']}/instantiate",
                            params={
                                "user_usecase": user_usecase,
                                "extra_mcps": extra_mcp_ids,
                                "extra_plugins": extra_plugin_ids,
                            },
                            timeout=60,
                        ).json()
                    if r and r.get("success"):
                        st.session_state.active_agent_id   = r["agent_id"]
                        st.session_state.active_agent_name = tmpl["name"]
                        st.session_state.messages          = []
                        st.session_state.session_id        = str(uuid.uuid4())
                        st.session_state.instantiate_template = None
                        st.success(f"✅ {tmpl['name']} ready: `{r['agent_id']}`")
                        ui_url = f"http://localhost:{API_PORT}/agents/{r['agent_id']}/ui"
                        st.info(f"🌐 Direct URL: [{ui_url}]({ui_url})")
                        if r.get("agent_folder"):
                            st.caption(f"📁 `{r['agent_folder']}`")
                        st.balloons()
                        st.session_state.page = "chat"
                        st.rerun()
                    else:
                        err = r.get("error") if r else "API unreachable"
                        st.error(f"Failed: {err}")


# ═════════════════════════════════════════════════════════════════════════════
# PAGE 2 — CHAT
# ═════════════════════════════════════════════════════════════════════════════

elif st.session_state.page == "chat":

    agents = get_agents()

    top_left, top_right = st.columns([4, 2])

    with top_left:
        if not agents:
            st.warning("No agents created yet. Go to **Home** and pick or create one.")
            st.stop()

        agent_options = {
            f"{a['agent_id']}  ·  {a['name']}  ·  {a['model']}": a["agent_id"]
            for a in agents
        }
        current_label = None
        if st.session_state.active_agent_id:
            for label, aid in agent_options.items():
                if aid == st.session_state.active_agent_id:
                    current_label = label
                    break

        st.markdown("#### Select agent")
        st.caption("🔍 Type to search by name, ID, or model")
        selected_label = st.selectbox(
            "Agent",
            options=list(agent_options.keys()),
            index=list(agent_options.keys()).index(current_label) if current_label else 0,
            placeholder="Search agents...",
            label_visibility="collapsed",
        )
        selected_agent_id = agent_options[selected_label]

        if selected_agent_id != st.session_state.active_agent_id:
            st.session_state.active_agent_id   = selected_agent_id
            st.session_state.active_agent_name = next(
                (a["name"] for a in agents if a["agent_id"] == selected_agent_id), selected_agent_id
            )
            st.session_state.messages  = []
            st.session_state.session_id = str(uuid.uuid4())
            st.rerun()

    with top_right:
        st.markdown("#### Session")
        ca, cb = st.columns(2)
        with ca:
            if st.button("🗑️ Clear memory", use_container_width=True):
                api_delete(f"/agents/{selected_agent_id}/memory/{st.session_state.session_id}")
                st.session_state.messages   = []
                st.session_state.session_id = str(uuid.uuid4())
                st.rerun()
        with cb:
            detail = api_get(f"/agents/{selected_agent_id}")
            if detail:
                mcps    = detail.get("selected_mcp_ids", [])
                plugins = detail.get("selected_plugin_ids", [])
                if mcps:
                    st.caption(f"MCPs: {', '.join(mcps)}")
                if plugins:
                    st.caption(f"Plugins: {', '.join(plugins)}")
                ui_url = f"http://localhost:{API_PORT}/agents/{selected_agent_id}/ui"
                st.markdown(f"[🌐 Open direct URL]({ui_url})")
                if detail.get("deployed"):
                    st.caption("Deployed as standalone folder")
                elif st.button("Deploy standalone", use_container_width=True):
                    deployed = api_post(f"/agents/{selected_agent_id}/deploy", {})
                    if deployed and deployed.get("success"):
                        st.success(f"Deployed to `{deployed.get('agent_folder')}`")
                        st.rerun()
                    else:
                        st.error(deployed.get("error") if deployed else "Deploy failed")

    st.divider()

    chat_col, status_col = st.columns([5, 1])

    with status_col:
        st.markdown("**MCP Status**")
        for srv in get_mcp_status():
            if srv["running"] and srv.get("initialized"):
                icon = "🟢"
            elif srv["running"]:
                icon = "🟡"
            else:
                icon = "🔴"
            tools = srv.get("tools", [])
            with st.expander(f"{icon} {srv['name']}", expanded=False):
                if tools:
                    for t in tools:
                        st.caption(f"• {t}")
                else:
                    st.caption("No tools discovered yet")

    with chat_col:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("tool_calls"):
                    with st.expander(f"🔧 {len(msg['tool_calls'])} tool call(s)", expanded=False):
                        for tc in msg["tool_calls"]:
                            st.json(tc)
                if msg.get("meta"):
                    st.caption(msg["meta"])

        agent_display_name = st.session_state.active_agent_name or selected_agent_id
        if prompt := st.chat_input(f"Message {agent_display_name}..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    r = api_post("/agent/run", {
                        "session_id": st.session_state.session_id,
                        "agent_id":   selected_agent_id,
                        "message":    prompt,
                    }, timeout=300)

                if r and r.get("success") and r.get("response"):
                    resp = r["response"]
                    st.markdown(resp["message"])
                    meta = (
                        f"Iter: {resp.get('iterations_used')} | "
                        f"{resp.get('duration_ms')}ms | "
                        f"{resp.get('status')}"
                    )
                    st.caption(meta)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": resp["message"],
                        "tool_calls": resp.get("tool_calls", []),
                        "meta": meta,
                    })
                else:
                    err = (r.get("error") if r else None) or "API unreachable or timed out"
                    st.error(err)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": f"Error: {err}"}
                    )
            st.rerun()
