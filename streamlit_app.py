"""
streamlit_app.py — Framework Streamlit UI
Connects to FastAPI only — never directly to executor.
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

st.set_page_config(page_title="Agentic Framework", page_icon="🤖", layout="wide")
st.title("🤖 Agentic Framework — Base Layer")

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Agent configuration")

    try:
        reg = requests.get(f"{API}/mcp/registry", timeout=3).json()
        sel = {s["name"]: s["id"] for s in reg.get("selectable", [])}
    except Exception:
        sel = {}
        st.warning("API offline — MCP list unavailable")

    agent_name = st.text_input("Agent name", value="My Agent")
    usecase = st.text_area(
        "Use-case context",
        value="You help the team manage GitHub repositories and answer code questions.",
        height=120,
    )
    chosen = st.multiselect("Enable MCP servers", list(sel))
    chosen_ids = [sel[n] for n in chosen]

    if st.button("Create agent", type="primary"):
        try:
            r = requests.post(f"{API}/agents", json={
                "name": agent_name,
                "usecase_context": usecase,
                "selected_mcp_ids": chosen_ids,
            }, timeout=30).json()

            if r.get("success"):
                st.session_state["agent_id"] = r["agent_id"]
                st.session_state["agent_folder"] = r.get("agent_folder", "")
                st.success(f"✅ Created: `{r['agent_id']}`")
                if r.get("agent_folder"):
                    st.info(f"📁 Agent folder:\n`{r['agent_folder']}`")
                    st.caption("This folder is fully self-contained and can run independently.")
            else:
                st.error(r.get("error", "Unknown error"))
        except Exception as e:
            st.error(f"Request failed: {e}")

    st.divider()

    if st.session_state.get("agent_id"):
        st.caption(f"Active agent: `{st.session_state['agent_id']}`")
        if st.session_state.get("agent_folder"):
            st.caption(f"📁 `{st.session_state['agent_folder']}`")

    if st.button("Clear session memory"):
        sid = st.session_state.get("session_id", "")
        if sid:
            requests.delete(f"{API}/agent/session/{sid}", timeout=5)
        st.session_state["messages"] = []
        st.rerun()

    st.divider()

    try:
        status = requests.get(f"{API}/mcp/status", timeout=2).json()
        st.subheader("MCP Status")
        for srv in status.get("active_servers", []):
            if srv["running"] and srv.get("initialized"):
                icon = "🟢"
            elif srv["running"]:
                icon = "🟡"
            else:
                icon = "🔴"
            tools = srv.get("tools", [])
            label = f"{icon} {srv['name']}"
            if tools:
                label += f" ({len(tools)} tools)"
            st.caption(label)
    except Exception:
        st.caption("⚫ API offline")

# ─── Main chat ─────────────────────────────────────────────────────────────────

if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state["messages"] = []

for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("tool_calls"):
            with st.expander(f"🔧 Tool calls ({len(msg['tool_calls'])})", expanded=False):
                for tc in msg["tool_calls"]:
                    st.json(tc)
        if msg.get("meta"):
            st.caption(msg["meta"])

if prompt := st.chat_input("Message the agent..."):
    agent_id = st.session_state.get("agent_id")
    if not agent_id:
        st.error("Create an agent first using the sidebar.")
        st.stop()

    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                r = requests.post(f"{API}/agent/run", json={
                    "session_id": st.session_state["session_id"],
                    "agent_id": agent_id,
                    "message": prompt,
                }, timeout=300).json()

                if r.get("success") and r.get("response"):
                    resp = r["response"]
                    st.markdown(resp["message"])
                    meta = (
                        f"Iterations: {resp.get('iterations_used')} | "
                        f"Duration: {resp.get('duration_ms')}ms | "
                        f"Status: {resp.get('status')}"
                    )
                    st.caption(meta)
                    st.session_state["messages"].append({
                        "role": "assistant",
                        "content": resp["message"],
                        "tool_calls": resp.get("tool_calls", []),
                        "meta": meta,
                    })
                else:
                    err = r.get("error", "Unknown error")
                    st.error(err)
                    st.session_state["messages"].append(
                        {"role": "assistant", "content": f"Error: {err}"}
                    )
            except Exception as e:
                st.error(f"API error: {e}")
