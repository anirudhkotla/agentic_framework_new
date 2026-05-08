"""
core/agent_folder.py
--------------------
Writes a fully self-contained, independently runnable agent codebase
to agents/{agent_id}/ every time an agent is created.

Folder structure:
    agents/{agent_id}/
    ├── .env.template       ← only vars this agent actually needs
    ├── requirements.txt
    ├── README.md
    ├── main.py             ← standalone entrypoint
    ├── streamlit_ui.py     ← standalone Streamlit UI
    ├── agent_config.json   ← full config snapshot
    ├── mcp/                ← copied from framework
    ├── prompts/            ← copied from framework
    ├── core/               ← copied from framework
    └── api/                ← copied from framework
"""

from __future__ import annotations

import json
import logging
import shutil
import textwrap
from pathlib import Path

from core.models import AgentConfig

logger = logging.getLogger(__name__)

_FW_ROOT = Path(__file__).parent.parent
_AGENTS_DIR = _FW_ROOT / "agents"


# ─── Main entry point ────────────────────────────────────────────────────────

def write_agent_folder(config: AgentConfig, registry_data: dict) -> Path:
    folder = _AGENTS_DIR / config.agent_id
    folder.mkdir(parents=True, exist_ok=True)

    _copy_source_files(folder)
    _write_config_json(folder, config)
    _write_filtered_registry(folder, config, registry_data)
    _write_env_template(folder, config, registry_data)
    _write_requirements(folder)
    _write_readme(folder, config, registry_data)
    _write_main_py(folder, config)
    _write_streamlit_ui(folder, config)

    logger.info("Agent folder written → %s", folder)
    return folder


# ─── Source file copier ──────────────────────────────────────────────────────

def _copy_source_files(folder: Path):
    for d in ["mcp", "prompts", "core", "api"]:
        dest = folder / d
        dest.mkdir(exist_ok=True)
        (dest / "__init__.py").touch()
        for f in (_FW_ROOT / d).iterdir():
            if f.suffix == ".py":
                shutil.copy2(f, dest / f.name)
    shutil.copy2(_FW_ROOT / "mcp" / "registry.json", folder / "mcp" / "registry.json")


# ─── agent_config.json ───────────────────────────────────────────────────────

def _write_config_json(folder: Path, config: AgentConfig):
    (folder / "agent_config.json").write_text(
        json.dumps(config.model_dump(), indent=2, default=str)
    )


# ─── Filtered registry ───────────────────────────────────────────────────────

def _write_filtered_registry(folder: Path, config: AgentConfig, registry_data: dict):
    sel_ids = set(config.selected_mcp_ids)
    filtered_selectable = [
        s for s in registry_data["layers"]["selectable"]["servers"]
        if s["id"] in sel_ids
    ]
    filtered = {
        "version": registry_data.get("version", "1.0.0"),
        "description": f"Registry for agent: {config.name} ({config.agent_id})",
        "layers": {
            "base": registry_data["layers"]["base"],
            "selectable": {
                "description": "Selected servers for this agent",
                "servers": filtered_selectable,
            },
        },
        "builtin_skills": registry_data.get("builtin_skills", []),
    }
    (folder / "mcp" / "registry.json").write_text(json.dumps(filtered, indent=2))


# ─── .env.template ───────────────────────────────────────────────────────────

def _write_env_template(folder: Path, config: AgentConfig, registry_data: dict):
    sel_map = {s["id"]: s for s in registry_data["layers"]["selectable"]["servers"]}

    lines = [
        "# ================================================================",
        f"# Agent : {config.name}",
        f"# ID    : {config.agent_id}",
        f"# Model : {config.model_name}",
        "# ================================================================",
        "# Copy to .env and fill in your values. Never commit .env to git.",
        "",
        "# ── LLM [REQUIRED] ──────────────────────────────────────────────",
        "# https://console.mistral.ai/api-keys",
        "MISTRAL_API_KEY=",
        "",
        "# ── LLM settings ────────────────────────────────────────────────",
        f"DEFAULT_MODEL={config.model_name}",
        "",
        "# ── App settings ────────────────────────────────────────────────",
        "API_HOST=0.0.0.0",
        "API_PORT=8002",
        "STREAMLIT_PORT=8502",
        "LOG_LEVEL=INFO",
        "",
        "# ── User context (used in system prompt) ────────────────────────",
        "GITHUB_USERNAME=",
        "",
        "# ── MCP base layer ──────────────────────────────────────────────",
        "# run: npm root -g  → paste output here",
        "NPM_GLOBAL_ROOT=",
        "# path to a git repo this agent operates on",
        "GIT_REPO_PATH=",
        "AGENT_WORKSPACE=/tmp/agent_workspace",
        "",
    ]

    added_cats: set[str] = set()
    for sid in config.selected_mcp_ids:
        srv = sel_map.get(sid)
        if not srv or not srv.get("required_env"):
            continue
        cat = srv.get("category", "other").title()
        if cat not in added_cats:
            lines.append(f"# ── {cat} MCPs ──────────────────────────────────────────────")
            added_cats.add(cat)
        lines.append(f"# {srv['name']}")
        for var in srv["required_env"]:
            lines.append(f"{var}=")
        lines.append("")

    (folder / ".env.template").write_text("\n".join(lines) + "\n")


# ─── requirements.txt ────────────────────────────────────────────────────────

def _write_requirements(folder: Path):
    (folder / "requirements.txt").write_text(
        "langchain>=0.3.0\n"
        "langchain-mistralai>=0.2.0\n"
        "langchain-core>=0.3.0\n"
        "pydantic>=2.0.0\n"
        "fastapi>=0.115.0\n"
        "uvicorn[standard]>=0.32.0\n"
        "streamlit>=1.40.0\n"
        "python-dotenv>=1.0.0\n"
        "httpx>=0.27.0\n"
        "requests>=2.32.0\n"
    )


# ─── README.md ───────────────────────────────────────────────────────────────

def _write_readme(folder: Path, config: AgentConfig, registry_data: dict):
    sel_map = {s["id"]: s["name"] for s in registry_data["layers"]["selectable"]["servers"]}
    base_names = [s["name"] for s in registry_data["layers"]["base"]["servers"]]
    selected_names = [sel_map[sid] for sid in config.selected_mcp_ids if sid in sel_map]

    (folder / "README.md").write_text(textwrap.dedent(f"""\
        # {config.name}

        > Agent ID: `{config.agent_id}` | Model: `{config.model_name}`

        ## What this agent does
        {config.usecase_context}

        ## Tools loaded
        **Base layer (always active)**
        {chr(10).join(f"- {n}" for n in base_names)}

        **Selected for this agent**
        {chr(10).join(f"- {n}" for n in selected_names) if selected_names else "- None"}

        ---

        ## Setup on any machine

        ```bash
        # 1. Prerequisites: Python 3.12+, Node.js 18+, uv
        curl -LsSf https://astral.sh/uv/install.sh | sh

        # 2. One-time npm installs
        npm install -g @modelcontextprotocol/server-filesystem
        npm install -g @modelcontextprotocol/server-memory
        npm install -g @modelcontextprotocol/server-github

        # 3. Python environment
        uv venv .venv
        source .venv/bin/activate
        uv pip install -r requirements.txt

        # 4. Configure
        cp .env.template .env
        nano .env   # fill in MISTRAL_API_KEY, NPM_GLOBAL_ROOT, GIT_REPO_PATH
        ```

        ## Run

        ```bash
        python main.py chat        # interactive terminal chat
        python main.py api         # REST API on port 8002
        python main.py streamlit   # Streamlit UI on port 8502
        python main.py both        # API + Streamlit simultaneously
        ```

        ## This folder is fully self-contained
        No dependency on the framework that generated it.
        Copy anywhere, fill in `.env`, run `python main.py`.
    """))


# ─── main.py ─────────────────────────────────────────────────────────────────

def _write_main_py(folder: Path, config: AgentConfig):
    """
    Write main.py as a clean Python source file using textwrap.dedent.
    No string-list building, no escaping issues.
    """
    agent_id = config.agent_id
    agent_name = config.name
    selected_ids_repr = repr(config.selected_mcp_ids)

    content = textwrap.dedent(f'''\
        """
        main.py — Standalone entrypoint for agent: {agent_name}
        Agent ID: {agent_id}

        Usage:
            python main.py chat        # interactive terminal chat
            python main.py api         # FastAPI REST server on API_PORT
            python main.py streamlit   # Streamlit UI on STREAMLIT_PORT
            python main.py both        # API + Streamlit simultaneously
        """

        from __future__ import annotations

        import asyncio
        import json
        import logging
        import os
        import socket
        import subprocess
        import sys
        import threading
        import uuid
        from pathlib import Path

        from dotenv import load_dotenv

        # Load .env from this folder
        load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
        logger = logging.getLogger(__name__)

        sys.path.insert(0, str(Path(__file__).parent))

        logger.info("Agent      : {agent_name} ({agent_id})")
        logger.info("Model      : %s", os.getenv("DEFAULT_MODEL", "mistral-large-latest"))
        logger.info("NPM root   : %s", os.getenv("NPM_GLOBAL_ROOT", "NOT SET"))
        logger.info("Git repo   : %s", os.getenv("GIT_REPO_PATH", "NOT SET"))

        from mcp.host import MCPHost
        from core.executor import MistralExecutor, SessionMemory
        from core.models import AgentConfig, UserInput, InputType

        _CONFIG = AgentConfig(**json.loads((Path(__file__).parent / "agent_config.json").read_text()))

        SELECTED_IDS = {selected_ids_repr}


        def _bootstrap():
            host = MCPHost()
            results = host.start(selected_ids=SELECTED_IDS)
            for sid, ok in results.items():
                logger.info("MCP [%s]: %s", sid, "OK" if ok else "FAILED")
            memory = SessionMemory()
            executor = MistralExecutor(host, memory)
            return host, executor, memory


        # ── Chat mode ─────────────────────────────────────────────────────────

        def run_chat():
            host, executor, memory = _bootstrap()
            session_id = str(uuid.uuid4())
            print("\\n" + "=" * 60)
            print(f"Agent : {{_CONFIG.name}}")
            print(f"Model : {{_CONFIG.model_name}}")
            print("Commands: 'quit' to exit | 'clear' to reset memory")
            print("=" * 60 + "\\n")

            async def _loop():
                while True:
                    try:
                        msg = input("You: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\\nGoodbye!")
                        break
                    if not msg:
                        continue
                    if msg.lower() in ("quit", "exit"):
                        print("Goodbye!")
                        break
                    if msg.lower() == "clear":
                        memory.clear(session_id)
                        print("[Memory cleared]\\n")
                        continue
                    user_input = UserInput(
                        session_id=session_id,
                        agent_id=_CONFIG.agent_id,
                        content=msg,
                    )
                    print("\\nAgent: ", end="", flush=True)
                    response = await executor.run(_CONFIG, user_input)
                    print(response.message)
                    if response.tool_calls:
                        print(f"  [Tool calls: {{len(response.tool_calls)}}]")
                    print(f"  [{{response.iterations_used}} iter | {{response.duration_ms}}ms]\\n")

            asyncio.run(_loop())
            host.stop()


        # ── API mode ──────────────────────────────────────────────────────────

        def run_api():
            import uvicorn
            from fastapi import FastAPI
            from fastapi.middleware.cors import CORSMiddleware
            from fastapi.responses import StreamingResponse
            from core.models import (
                RunAgentRequest, RunAgentResponse,
                HealthResponse, MCPStatusResponse,
            )

            api_host = os.getenv("API_HOST", "0.0.0.0")
            api_port = int(os.getenv("API_PORT", "8002"))

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("localhost", api_port)) == 0:
                    logger.error(
                        "Port %s in use. Run: sudo lsof -ti :%s | xargs kill -9",
                        api_port, api_port,
                    )
                    sys.exit(1)

            host, executor, memory = _bootstrap()

            app = FastAPI(title=_CONFIG.name, version="1.0.0", docs_url="/api/docs")
            app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"], allow_credentials=True,
                allow_methods=["*"], allow_headers=["*"],
            )

            @app.get("/api/v1/health", response_model=HealthResponse)
            async def health():
                s = host.status()
                return HealthResponse(
                    status="ok", version="1.0.0",
                    active_agents=1, active_mcp_servers=s["total_active"],
                )

            @app.get("/api/v1/mcp/status", response_model=MCPStatusResponse)
            async def mcp_status():
                return MCPStatusResponse(**host.status())

            @app.post("/api/v1/agent/run", response_model=RunAgentResponse)
            async def run_agent(req: RunAgentRequest):
                try:
                    user_input = UserInput(
                        session_id=req.session_id, agent_id=req.agent_id,
                        content=req.message, input_type=req.input_type,
                    )
                    resp = await executor.run(_CONFIG, user_input)
                    return RunAgentResponse(success=True, response=resp)
                except Exception as e:
                    return RunAgentResponse(success=False, error=str(e))

            @app.post("/api/v1/agent/stream")
            async def stream_agent(req: RunAgentRequest):
                async def _gen():
                    user_input = UserInput(
                        session_id=req.session_id, agent_id=req.agent_id,
                        content=req.message,
                    )
                    async for tok in executor.stream(_CONFIG, user_input):
                        yield f"data: {{tok}}\\n\\n"
                    yield "data: [DONE]\\n\\n"
                return StreamingResponse(
                    _gen(), media_type="text/event-stream",
                    headers={{"Cache-Control": "no-cache"}},
                )

            @app.delete("/api/v1/agent/session/{{session_id}}")
            async def clear_session(session_id: str):
                memory.clear(session_id)
                return {{"success": True}}

            logger.info("API  → http://%s:%s", api_host, api_port)
            logger.info("Docs → http://%s:%s/api/docs", api_host, api_port)
            uvicorn.run(app, host=api_host, port=api_port)


        # ── Streamlit mode ────────────────────────────────────────────────────

        def run_streamlit():
            port = os.getenv("STREAMLIT_PORT", "8502")
            subprocess.run(
                ["streamlit", "run", "streamlit_ui.py", "--server.port", port],
                check=True,
            )


        def run_both():
            t = threading.Thread(target=run_api, daemon=True)
            t.start()
            run_streamlit()


        # ── Entrypoint ────────────────────────────────────────────────────────

        if __name__ == "__main__":
            mode = sys.argv[1] if len(sys.argv) > 1 else "chat"
            modes = {{
                "chat": run_chat,
                "api": run_api,
                "streamlit": run_streamlit,
                "both": run_both,
            }}
            if mode not in modes:
                print(f"Unknown mode: {{mode}}. Choose: {{list(modes)}}")
                sys.exit(1)
            modes[mode]()
    ''')

    (folder / "main.py").write_text(content)


# ─── streamlit_ui.py ─────────────────────────────────────────────────────────

def _write_streamlit_ui(folder: Path, config: AgentConfig):
    agent_id = config.agent_id
    agent_name = config.name
    agent_model = config.model_name

    content = textwrap.dedent(f'''\
        """
        streamlit_ui.py — Standalone Streamlit UI for: {agent_name}
        Run via: python main.py streamlit
        (requires API running: python main.py api)
        """

        import os
        import uuid
        import requests
        import streamlit as st
        from dotenv import load_dotenv
        from pathlib import Path

        load_dotenv(Path(__file__).parent / ".env")

        API_PORT = os.getenv("API_PORT", "8002")
        API = f"http://localhost:{{API_PORT}}/api/v1"
        AGENT_ID = "{agent_id}"

        st.set_page_config(page_title="{agent_name}", page_icon="🤖", layout="wide")
        st.title("🤖 {agent_name}")
        st.caption(f"Agent: `{agent_id}` | Model: `{agent_model}`")

        with st.sidebar:
            st.header("Session")
            if st.button("Clear memory"):
                sid = st.session_state.get("session_id", "")
                if sid:
                    requests.delete(f"{{API}}/agent/session/{{sid}}", timeout=5)
                st.session_state["messages"] = []
                st.rerun()

            st.divider()
            try:
                s = requests.get(f"{{API}}/mcp/status", timeout=2).json()
                st.subheader("MCP Status")
                for srv in s.get("active_servers", []):
                    if srv["running"] and srv.get("initialized"):
                        icon = "🟢"
                    elif srv["running"]:
                        icon = "🟡"
                    else:
                        icon = "🔴"
                    tools = srv.get("tools", [])
                    label = f"{{icon}} {{srv[\'name\']}}"
                    if tools:
                        label += f" ({{len(tools)}} tools)"
                    st.caption(label)
            except Exception:
                st.caption("⚫ API offline")
                st.caption("Run: python main.py api")

        if "session_id" not in st.session_state:
            st.session_state["session_id"] = str(uuid.uuid4())
        if "messages" not in st.session_state:
            st.session_state["messages"] = []

        for msg in st.session_state["messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("tool_calls"):
                    with st.expander(f"🔧 Tool calls ({{len(msg[\'tool_calls\'])}})", expanded=False):
                        for tc in msg["tool_calls"]:
                            st.json(tc)
                if msg.get("meta"):
                    st.caption(msg["meta"])

        if prompt := st.chat_input("Message the agent..."):
            st.session_state["messages"].append({{"role": "user", "content": prompt}})
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        r = requests.post(
                            f"{{API}}/agent/run",
                            json={{
                                "session_id": st.session_state["session_id"],
                                "agent_id": AGENT_ID,
                                "message": prompt,
                            }},
                            timeout=300,
                        ).json()
                        if r.get("success") and r.get("response"):
                            resp = r["response"]
                            st.markdown(resp["message"])
                            meta = (
                                f"Iter: {{resp.get(\'iterations_used\')}} | "
                                f"{{resp.get(\'duration_ms\')}}ms | "
                                f"{{resp.get(\'status\')}}"
                            )
                            st.caption(meta)
                            st.session_state["messages"].append({{
                                "role": "assistant",
                                "content": resp["message"],
                                "tool_calls": resp.get("tool_calls", []),
                                "meta": meta,
                            }})
                        else:
                            err = r.get("error", "Unknown error")
                            st.error(err)
                            st.session_state["messages"].append(
                                {{"role": "assistant", "content": f"Error: {{err}}"}}
                            )
                    except Exception as e:
                        st.error(f"API connection failed: {{e}}")
    ''')

    (folder / "streamlit_ui.py").write_text(content)