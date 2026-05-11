"""
api/routes.py — FastAPI REST API, all endpoints under /api/v1/
Includes plugin management endpoints under /api/v1/plugins/
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from core.agent_manager import AgentManager
from core.models import (
    CreateAgentRequest, CreateAgentResponse,
    HealthResponse, MCPStatusResponse,
    RunAgentRequest, RunAgentResponse,
)
from mcp.host import MCPHost, load_registry
from api.agent_ui import register_agent_ui_routes

logger = logging.getLogger(__name__)


def create_app(mcp_host: MCPHost, agent_manager: AgentManager) -> FastAPI:
    app = FastAPI(
        title="Agentic Framework API",
        description="Internal agent builder — base layer with plugin support",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    async def health():
        s = mcp_host.status()
        return HealthResponse(
            status="ok", version="1.0.0",
            active_agents=len(agent_manager.list_agents()),
            active_mcp_servers=s["total_active"],
        )

    # ── MCP ───────────────────────────────────────────────────────────────────

    @app.get("/api/v1/mcp/status", response_model=MCPStatusResponse, tags=["mcp"])
    async def mcp_status():
        return MCPStatusResponse(**mcp_host.status())

    @app.get("/api/v1/mcp/registry", tags=["mcp"])
    async def mcp_registry():
        reg = load_registry()
        return {
            "base": [s.model_dump() for s in reg.base_servers],
            "selectable": [s.model_dump() for s in reg.selectable_servers],
            "builtin_skills": [s.model_dump() for s in reg.builtin_skills],
        }

    # ── Plugins ───────────────────────────────────────────────────────────────

    @app.get("/api/v1/plugins", tags=["plugins"])
    async def list_plugins():
        """List all installed plugins (community + user)."""
        from plugins.plugin_registry import get_plugin_registry
        reg = get_plugin_registry()
        return {"plugins": reg.to_api_list()}

    @app.get("/api/v1/plugins/search", tags=["plugins"])
    async def search_plugins(q: str = ""):
        """Search plugins by name, description, tags, category."""
        from plugins.plugin_registry import get_plugin_registry
        reg = get_plugin_registry()
        if not q:
            return {"plugins": reg.to_api_list()}
        results = reg.search(q)
        return {
            "plugins": [
                {
                    "id": p.id, "name": p.name, "version": p.version,
                    "type": p.type.value, "category": p.category,
                    "description": p.description, "author": p.author,
                    "tags": p.tags, "source": p.source,
                    "required_env": p.required_env,
                }
                for p in results
            ],
            "query": q,
            "count": len(results),
        }

    @app.get("/api/v1/plugins/{plugin_id}", tags=["plugins"])
    async def get_plugin(plugin_id: str):
        """Get details for a specific plugin."""
        from plugins.plugin_registry import get_plugin_registry
        reg = get_plugin_registry()
        plugin = reg.get(plugin_id)
        if not plugin:
            raise HTTPException(404, f"Plugin '{plugin_id}' not found")
        return plugin.model_dump()

    @app.post("/api/v1/plugins/upload", tags=["plugins"])
    async def upload_plugin(file: UploadFile = File(...)):
        """
        Upload a .plugin.json file to install a user plugin.
        The file is validated against the plugin schema before saving.
        """
        from plugins.plugin_registry import get_plugin_registry
        from plugins.plugin_schema import validate_plugin_json

        if not file.filename or not file.filename.endswith(".json"):
            raise HTTPException(400, "File must be a .json file")

        try:
            content = await file.read()
            raw = json.loads(content.decode())
        except Exception as e:
            raise HTTPException(400, f"Invalid JSON: {e}")

        valid, err = validate_plugin_json(raw)
        if not valid:
            raise HTTPException(422, f"Plugin validation failed: {err}")

        reg = get_plugin_registry()
        success, msg = reg.install(raw)
        if not success:
            raise HTTPException(400, msg)

        return {"success": True, "message": msg, "plugin_id": raw.get("id")}

    @app.post("/api/v1/plugins/validate", tags=["plugins"])
    async def validate_plugin(payload: dict):
        """Validate a plugin JSON without installing it. Returns errors if invalid."""
        from plugins.plugin_schema import validate_plugin_json
        valid, err = validate_plugin_json(payload)
        return {"valid": valid, "error": err if not valid else None}

    @app.delete("/api/v1/plugins/{plugin_id}", tags=["plugins"])
    async def uninstall_plugin(plugin_id: str):
        """Remove a user-installed plugin. Community plugins cannot be removed."""
        from plugins.plugin_registry import get_plugin_registry
        reg = get_plugin_registry()
        success, msg = reg.uninstall(plugin_id)
        if not success:
            raise HTTPException(400, msg)
        return {"success": True, "message": msg}

    @app.post("/api/v1/plugins/reload", tags=["plugins"])
    async def reload_plugins():
        """Force re-scan of plugin directories. Call after manually adding files."""
        from plugins.plugin_registry import get_plugin_registry
        reg = get_plugin_registry()
        reg.reload()
        return {"success": True, "total": len(reg.all())}


    # ── Defined agents ────────────────────────────────────────────────────────

    @app.get("/api/v1/defined-agents", tags=["defined-agents"])
    async def list_defined_agents():
        """List all defined agent templates."""
        from defined_agents.loader import load_all
        return {"defined_agents": load_all()}

    @app.get("/api/v1/defined-agents/{agent_id}", tags=["defined-agents"])
    async def get_defined_agent(agent_id: str):
        """Get a single defined agent template."""
        from defined_agents.loader import get
        template = get(agent_id)
        if not template:
            raise HTTPException(404, f"Defined agent '{agent_id}' not found")
        return template

    @app.post("/api/v1/defined-agents/{agent_id}/instantiate", tags=["defined-agents"])
    async def instantiate_defined_agent(
        agent_id: str,
        user_usecase: str = "",
        extra_mcps: list[str] = [],
        extra_plugins: list[str] = [],
    ):
        """
        Create an agent instance from a defined agent template.
        Merges template defaults with user overrides.
        """
        from defined_agents.loader import get, to_create_request
        from core.models import CreateAgentRequest
        template = get(agent_id)
        if not template:
            raise HTTPException(404, f"Defined agent '{agent_id}' not found")
        req_dict = to_create_request(template, user_usecase, extra_mcps, extra_plugins)
        req = CreateAgentRequest(**req_dict)
        try:
            config, folder = agent_manager.create(req)
            return CreateAgentResponse(
                success=True,
                agent_id=config.agent_id,
                config=config,
                agent_folder=str(folder),
            )
        except Exception as e:
            logger.exception("Failed to instantiate defined agent")
            return CreateAgentResponse(success=False, error=str(e))

    # ── Agents ────────────────────────────────────────────────────────────────

    @app.get("/api/v1/agents", tags=["agents"])
    async def list_agents():
        return {"agents": agent_manager.list_agents()}

    @app.post("/api/v1/agents", response_model=CreateAgentResponse, tags=["agents"])
    async def create_agent(req: CreateAgentRequest):
        try:
            config, folder = agent_manager.create(req)
            return CreateAgentResponse(
                success=True, agent_id=config.agent_id,
                config=config, agent_folder=str(folder),
            )
        except Exception as e:
            logger.exception("Failed to create agent")
            return CreateAgentResponse(success=False, error=str(e))

    @app.get("/api/v1/agents/{agent_id}", tags=["agents"])
    async def get_agent(agent_id: str):
        config = agent_manager.get(agent_id)
        if not config:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        return config.model_dump()

    @app.delete("/api/v1/agents/{agent_id}", tags=["agents"])
    async def delete_agent(agent_id: str):
        if not agent_manager.delete(agent_id):
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        return {"success": True, "deleted": agent_id}

    @app.get("/api/v1/agents/{agent_id}/folder", tags=["agents"])
    async def agent_folder_contents(agent_id: str):
        from pathlib import Path
        folder = Path(__file__).parent.parent / "agents" / agent_id
        if not folder.exists():
            raise HTTPException(404, f"Folder for '{agent_id}' not found on disk")
        files = {}
        for f in sorted(folder.rglob("*")):
            if f.is_file() and f.suffix in (".py", ".json", ".md", ".txt", ".template", ""):
                rel = str(f.relative_to(folder))
                try:
                    files[rel] = f.read_text()
                except Exception:
                    files[rel] = "<binary>"
        return {"agent_id": agent_id, "folder": str(folder), "files": files}

    # ── Run ───────────────────────────────────────────────────────────────────

    @app.post("/api/v1/agent/run", response_model=RunAgentResponse, tags=["run"])
    async def run_agent(req: RunAgentRequest):
        try:
            resp = await agent_manager.run(
                agent_id=req.agent_id, session_id=req.session_id,
                message=req.message, input_type=req.input_type,
                image_base64=req.image_base64,
            )
            return RunAgentResponse(success=True, response=resp)
        except Exception as e:
            logger.exception("Agent run failed")
            return RunAgentResponse(success=False, error=str(e))

    @app.post("/api/v1/agent/stream", tags=["run"])
    async def stream_agent(req: RunAgentRequest):
        async def gen() -> AsyncIterator[str]:
            async for tok in agent_manager.stream(req.agent_id, req.session_id, req.message):
                yield f"data: {tok}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/v1/agent/session/{session_id}", tags=["run"])
    async def clear_session(session_id: str):
        agent_manager.clear_session(session_id)
        return {"success": True, "cleared": session_id}

    # ── Debug ─────────────────────────────────────────────────────────────────

    @app.post("/api/v1/prompt/preview", tags=["debug"])
    async def preview_prompt(usecase_context: str = ""):
        from prompts.system_prompt import PromptBuilder
        rendered = PromptBuilder().build(
            tool_descriptions=mcp_host.get_tool_descriptions(),
            usecase_context=usecase_context or "No context provided.",
        )
        return {"rendered_prompt": rendered, "char_count": len(rendered)}

    # ── Per-agent UI routes ───────────────────────────────────────────────────
    register_agent_ui_routes(app, agent_manager)

    return app