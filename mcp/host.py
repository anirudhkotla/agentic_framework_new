"""
mcp/host.py
-----------
MCP Host — subprocess lifecycle manager with full JSON-RPC handshake.

Key behaviours:
- load_dotenv() at module level so vars resolve regardless of import order
- Validates no ${VAR} placeholders remain before launching any subprocess
- Auto-detects NPM_GLOBAL_ROOT via `npm root -g` if not set in .env
- Handshakes run in a background thread with its own event loop
  — never conflicts with FastAPI's uvloop
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(override=False)

logger = logging.getLogger(__name__)
REGISTRY_PATH = Path(__file__).parent / "registry.json"
_STARTUP_WAIT = 3.0


# ─── Pydantic models ──────────────────────────────────────────────────────────

class ServerConfig(BaseModel):
    id: str
    name: str
    category: str
    command: str
    args: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool
    description: str
    required_env: list[str] = Field(default_factory=list)


class BuiltinSkill(BaseModel):
    id: str
    name: str
    enabled: bool
    description: str


class Registry(BaseModel):
    version: str
    base_servers: list[ServerConfig] = Field(default_factory=list)
    selectable_servers: list[ServerConfig] = Field(default_factory=list)
    builtin_skills: list[BuiltinSkill] = Field(default_factory=list)


# ─── Registry loader ──────────────────────────────────────────────────────────

def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    raw = json.loads(path.read_text())
    return Registry(
        version=raw["version"],
        base_servers=[ServerConfig(**s) for s in raw["layers"]["base"]["servers"]],
        selectable_servers=[ServerConfig(**s) for s in raw["layers"]["selectable"]["servers"]],
        builtin_skills=[BuiltinSkill(**s) for s in raw["builtin_skills"]],
    )


# ─── Env resolver ─────────────────────────────────────────────────────────────

def _npm_global_root() -> str:
    try:
        r = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=5)
        val = r.stdout.strip()
        if val:
            logger.info("Auto-detected NPM_GLOBAL_ROOT: %s", val)
            return val
    except Exception:
        pass
    return "/usr/local/lib/node_modules"


def _resolve(value: str) -> str:
    """
    Resolve all ${VAR} placeholders inside a string.

    Handles pure placeholders AND embedded paths:
      "${NPM_GLOBAL_ROOT}"
      "${NPM_GLOBAL_ROOT}/@modelcontextprotocol/server-filesystem/dist/index.js"
    """
    import re

    def _replace(match):
        name = match.group(1)

        if name == "NPM_GLOBAL_ROOT":
            from_env = os.environ.get("NPM_GLOBAL_ROOT", "").strip()
            return from_env if from_env else _npm_global_root()

        if name == "AGENT_WORKSPACE":
            ws = os.environ.get("AGENT_WORKSPACE", "/tmp/agent_workspace").strip()
            os.makedirs(ws, exist_ok=True)
            return ws

        val = os.environ.get(name, "").strip()
        if not val:
            logger.warning("Env var %s not set", name)
            return match.group(0)  # keep original so bad-arg check catches it
        return val

    return re.sub(r"\$\{([^}]+)\}", _replace, value)


def _resolve_args(args: list[str]) -> list[str]:
    return [_resolve(a) for a in args]


def _resolve_env(template: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in template.items():
        resolved = _resolve(v)
        if resolved:
            out[k] = resolved
    return out


# ─── Server state ─────────────────────────────────────────────────────────────

class ServerState:
    def __init__(self, config: ServerConfig, proc: subprocess.Popen):
        self.config = config
        self.proc = proc
        self.tools: dict[str, dict] = {}
        self.initialized = False

    def running(self) -> bool:
        return self.proc.poll() is None


# ─── MCP Host ─────────────────────────────────────────────────────────────────

class MCPHost:
    def __init__(self, registry_path: Path = REGISTRY_PATH):
        self.registry = load_registry(registry_path)
        self._servers: dict[str, ServerState] = {}

    # ── Start / stop ──────────────────────────────────────────────────────────

    def start(self, selected_ids: list[str] | None = None) -> dict[str, bool]:
        to_start = list(self.registry.base_servers)
        sel_map = {s.id: s for s in self.registry.selectable_servers}
        for sid in (selected_ids or []):
            if sid in sel_map and sid not in self._servers:
                to_start.append(sel_map[sid])
            elif sid not in sel_map:
                logger.warning("MCP '%s' not in registry", sid)

        results: dict[str, bool] = {}
        new_ids: list[str] = []
        for cfg in to_start:
            if cfg.id in self._servers and self._servers[cfg.id].running():
                results[cfg.id] = True
                continue
            ok = self._launch(cfg)
            results[cfg.id] = ok
            if ok:
                new_ids.append(cfg.id)

        if new_ids:
            self._handshake_background(new_ids)
        return results

    def stop(self):
        for sid, state in self._servers.items():
            try:
                state.proc.terminate()
                state.proc.wait(timeout=5)
            except Exception as e:
                logger.error("Stop error [%s]: %s", sid, e)
        self._servers.clear()

    def _launch(self, cfg: ServerConfig) -> bool:
        env = {**os.environ, **_resolve_env(cfg.env)}
        cmd = [cfg.command] + _resolve_args(cfg.args)
        # Reject if any placeholder is still unresolved
        bad = [a for a in cmd if a.startswith("${")]
        if bad:
            logger.error(
                "MCP [%s] has unresolved args %s — check .env (NPM_GLOBAL_ROOT, GIT_REPO_PATH, etc.)",
                cfg.name, bad
            )
            return False
        logger.debug("Launching MCP [%s]: %s", cfg.name, " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )
            self._servers[cfg.id] = ServerState(cfg, proc)
            logger.info("Started MCP [%s] pid=%s", cfg.name, proc.pid)
            return True
        except FileNotFoundError:
            logger.error("MCP [%s] command not found: %s", cfg.name, cmd[0])
            return False
        except Exception as e:
            logger.error("MCP [%s] launch failed: %s", cfg.name, e)
            return False

    # ── Handshake (isolated background thread) ────────────────────────────────

    def _handshake_background(self, ids: list[str]):
        ready = threading.Event()
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._handshake_all(ids))
            finally:
                loop.close()
                ready.set()
        threading.Thread(target=_run, daemon=True).start()
        ready.wait(timeout=20)

    async def _handshake_all(self, ids: list[str]):
        await asyncio.gather(
            *[self._handshake(sid) for sid in ids if sid in self._servers],
            return_exceptions=True,
        )

    async def _handshake(self, sid: str):
        state = self._servers[sid]
        await asyncio.sleep(_STARTUP_WAIT)
        if not state.running():
            err = b""
            try:
                err = state.proc.stderr.read(600)
            except Exception:
                pass
            logger.error("MCP [%s] crashed.\n%s", sid, err.decode(errors="replace"))
            return

        loop = asyncio.get_event_loop()

        async def send(msg: dict) -> dict | None:
            data = (json.dumps(msg) + "\n").encode()
            try:
                await loop.run_in_executor(None, state.proc.stdin.write, data)
                await loop.run_in_executor(None, state.proc.stdin.flush)
                raw = await loop.run_in_executor(None, state.proc.stdout.readline)
                return json.loads(raw.decode()) if raw else None
            except Exception as e:
                logger.error("MCP send [%s]: %s", sid, e)
                return None

        try:
            r = await send({"jsonrpc":"2.0","id":1,"method":"initialize",
                "params":{"protocolVersion":"2024-11-05","capabilities":{},
                "clientInfo":{"name":"agent-fw","version":"1.0.0"}}})
            if not r or "error" in r:
                logger.error("MCP initialize failed [%s]: %s", sid, r)
                return

            notif = (json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n").encode()
            await loop.run_in_executor(None, state.proc.stdin.write, notif)
            await loop.run_in_executor(None, state.proc.stdin.flush)

            r = await send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
            if r and "result" in r:
                tools = r["result"].get("tools", [])
                state.tools = {t["name"]: t for t in tools}
                logger.info("MCP [%s] ready — tools: %s", sid, list(state.tools))
            else:
                logger.warning("MCP [%s] tools/list empty: %s", sid, r)
            state.initialized = True
        except Exception as e:
            logger.error("MCP handshake [%s]: %s", sid, e)

    # ── Tool call ──────────────────────────────────────────────────────────────

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> Any:
        state = self._servers.get(server_id)
        if state is None:
            raise ValueError(f"MCP '{server_id}' not registered")
        if not state.running():
            raise RuntimeError(f"MCP '{server_id}' process has exited")
        if not state.initialized:
            raise RuntimeError(f"MCP '{server_id}' not yet initialized")

        actual = tool_name if tool_name in state.tools else next(iter(state.tools), tool_name)
        req = {"jsonrpc":"2.0","id":3,"method":"tools/call",
               "params":{"name":actual,"arguments":arguments}}
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, state.proc.stdin.write, (json.dumps(req)+"\n").encode())
        await loop.run_in_executor(None, state.proc.stdin.flush)
        raw = await loop.run_in_executor(None, state.proc.stdout.readline)
        if not raw:
            raise RuntimeError(f"MCP '{server_id}' empty response")
        resp = json.loads(raw.decode())
        if "error" in resp:
            raise RuntimeError(f"MCP tool error [{server_id}]: {resp['error']}")
        return resp.get("result", {})

    # ── Status / introspection ─────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "active_servers": [
                {"id": sid, "name": s.config.name, "category": s.config.category,
                 "pid": s.proc.pid, "running": s.running(),
                 "initialized": s.initialized, "tools": list(s.tools.keys())}
                for sid, s in self._servers.items()
            ],
            "builtin_skills": [sk.model_dump() for sk in self.registry.builtin_skills],
            "total_active": sum(1 for s in self._servers.values() if s.running()),
        }

    def get_tool_descriptions(self) -> list[dict]:
        out = []
        for sid, state in self._servers.items():
            if not state.running():
                continue
            if state.initialized and state.tools:
                for tname, schema in state.tools.items():
                    out.append({
                        "server_id": sid, "tool_name": tname,
                        "lc_name": f"{sid}__{tname}",
                        "description": schema.get("description", state.config.description),
                        "category": state.config.category,
                        "input_schema": schema.get("inputSchema", {}),
                    })
            else:
                out.append({
                    "server_id": sid, "tool_name": sid,
                    "lc_name": sid.replace("-", "_"),
                    "description": state.config.description,
                    "category": state.config.category, "input_schema": {},
                })
        for sk in self.registry.builtin_skills:
            if sk.enabled:
                out.append({
                    "server_id": "builtin", "tool_name": sk.id,
                    "lc_name": sk.id, "description": sk.description,
                    "category": "builtin", "input_schema": {},
                })
        return out