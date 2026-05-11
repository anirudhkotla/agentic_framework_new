"""
main.py — Agentic Framework entrypoint

Modes:
  uv run python main.py api        → FastAPI on API_PORT
  uv run python main.py frontend   → Next.js UI on FRONTEND_PORT
  uv run python main.py both       → Both simultaneously
  uv run python main.py test       → pytest
"""

from __future__ import annotations

import os
import sys
from dotenv import load_dotenv

# Load .env using absolute path — works from any working directory
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=_ENV_PATH, override=True)

import logging
import subprocess
import threading
import socket

import uvicorn

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Log all key env vars so you can verify .env loaded correctly
logger.info("MISTRAL_API_KEY  = %s", "SET" if os.getenv("MISTRAL_API_KEY") else "NOT SET ← required")
logger.info("DEFAULT_MODEL    = %s", os.getenv("DEFAULT_MODEL", "NOT SET"))
logger.info("NPM_GLOBAL_ROOT  = %s", os.getenv("NPM_GLOBAL_ROOT", "NOT SET ← run: npm root -g"))
logger.info("GIT_REPO_PATH    = %s", os.getenv("GIT_REPO_PATH", "NOT SET ← any git repo path"))
logger.info("AGENT_WORKSPACE  = %s", os.getenv("AGENT_WORKSPACE", "/tmp/agent_workspace"))

from mcp.host import MCPHost
from core.agent_manager import AgentManager
from api.routes import create_app


def build_app():
    host = MCPHost()
    results = host.start(selected_ids=[])
    for sid, ok in results.items():
        logger.info("Base MCP [%s]: %s", sid, "OK" if ok else "FAILED")
    manager = AgentManager(mcp_host=host, executor_backend="mistral")
    app = create_app(mcp_host=host, agent_manager=manager)
    return host, manager, app


def _check_port(port: int):
    if _port_in_use(port):
        logger.error(
            "Port %s is already in use.\n"
            "  Run: sudo lsof -ti :%s | xargs kill -9\n"
            "  Or change API_PORT in .env",
            port, port
        )
        sys.exit(1)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _next_free_port(start: int) -> int:
    port = start
    while _port_in_use(port):
        port += 1
    return port


def run_api():
    api_host = os.getenv("API_HOST", "0.0.0.0")
    api_port = int(os.getenv("API_PORT", "8002"))
    _check_port(api_port)
    _, _, app = build_app()
    logger.info("API    → http://%s:%s", api_host, api_port)
    logger.info("Docs   → http://%s:%s/api/docs", api_host, api_port)
    uvicorn.run(app, host=api_host, port=api_port)


def run_streamlit():
    logger.warning("The Streamlit UI has moved to Next.js. Starting the frontend instead.")
    run_frontend()


def run_frontend():
    requested_port = int(os.getenv("FRONTEND_PORT", "3000"))
    port = _next_free_port(requested_port)
    api_port = os.getenv("API_PORT", "8002")
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
    env = {
        **os.environ,
        "NEXT_PUBLIC_API_BASE": os.getenv(
            "NEXT_PUBLIC_API_BASE",
            f"http://localhost:{api_port}/api/v1",
        ),
    }
    if port != requested_port:
        logger.warning(
            "Frontend port %s is already in use. Starting on %s instead.",
            requested_port,
            port,
        )
    logger.info("Frontend → http://0.0.0.0:%s", port)
    subprocess.run(
        ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", str(port)],
        cwd=frontend_dir,
        env=env,
        check=True,
    )


def run_both():
    t = threading.Thread(target=run_api, daemon=True)
    t.start()
    run_frontend()


def run_tests():
    subprocess.run(["python", "-m", "pytest", "tests/", "-v"], check=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "api"
    modes = {
        "api": run_api,
        "frontend": run_frontend,
        "streamlit": run_streamlit,
        "both": run_both,
        "test": run_tests,
    }
    if mode not in modes:
        print(f"Unknown mode '{mode}'. Choose: {list(modes)}")
        sys.exit(1)
    modes[mode]()
