"""
api/routes.py — FastAPI REST API, all endpoints under /api/v1/
"""

from __future__ import annotations
import logging
from typing import AsyncIterator
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from core.agent_manager import AgentManager
from core.models import (
    CreateAgentRequest, CreateAgentResponse,
    HealthResponse, MCPStatusResponse,
    RunAgentRequest, RunAgentResponse,
)
from mcp.host import MCPHost, load_registry

logger = logging.getLogger(__name__)


def create_app(mcp_host: MCPHost, agent_manager: AgentManager) -> FastAPI:
    app = FastAPI(
        title="Agentic Framework API",
        description="Internal agent builder — base layer",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    async def health():
        s = mcp_host.status()
        return HealthResponse(status="ok", version="1.0.0",
                               active_agents=len(agent_manager.list_agents()),
                               active_mcp_servers=s["total_active"])

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

    @app.get("/api/v1/agents", tags=["agents"])
    async def list_agents():
        return {"agents": agent_manager.list_agents()}

    @app.post("/api/v1/agents", response_model=CreateAgentResponse, tags=["agents"])
    async def create_agent(req: CreateAgentRequest):
        try:
            config, folder = agent_manager.create(req)
            return CreateAgentResponse(success=True, agent_id=config.agent_id,
                                        config=config, agent_folder=str(folder))
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
        return StreamingResponse(gen(), media_type="text/event-stream",
                                  headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.delete("/api/v1/agent/session/{session_id}", tags=["run"])
    async def clear_session(session_id: str):
        agent_manager.clear_session(session_id)
        return {"success": True, "cleared": session_id}

    @app.post("/api/v1/prompt/preview", tags=["debug"])
    async def preview_prompt(usecase_context: str = ""):
        from prompts.system_prompt import PromptBuilder
        rendered = PromptBuilder().build(
            tool_descriptions=mcp_host.get_tool_descriptions(),
            usecase_context=usecase_context or "No context provided.",
        )
        return {"rendered_prompt": rendered, "char_count": len(rendered)}

    @app.get("/api/v1/agents/{agent_id}/folder", tags=["agents"])
    async def agent_folder_contents(agent_id: str):
        from pathlib import Path
        folder = Path(__file__).parent.parent / "agents" / agent_id
        if not folder.exists():
            raise HTTPException(404, f"Folder for agent '{agent_id}' not found on disk")
        files = {}
        for f in sorted(folder.rglob("*")):
            if f.is_file() and f.suffix in (".py", ".json", ".md", ".txt", ".template", ""):
                rel = str(f.relative_to(folder))
                try:
                    files[rel] = f.read_text()
                except Exception:
                    files[rel] = "<binary>"
        return {"agent_id": agent_id, "folder": str(folder), "files": files}

    return app
