"""
core/agent_manager.py
---------------------
Registry of live agent instances.

On creation:
  1. Starts selected MCPs via MCPHost
  2. Starts MCP-type plugins via MCPHost (dynamic ServerConfig injection)
  3. Writes fully self-contained agent folder to agents/{agent_id}/
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from core.agent_folder import write_agent_folder
from core.executor import MistralExecutor, SessionMemory, get_executor
from core.models import (
    AgentConfig, AgentResponse, AgentStatus,
    CreateAgentRequest, InputType, UserInput,
)
from mcp.host import MCPHost, load_registry

logger = logging.getLogger(__name__)


class AgentManager:
    def __init__(self, mcp_host: MCPHost, executor_backend: str = "mistral"):
        self._mcp = mcp_host
        self._agents: dict[str, AgentConfig] = {}
        self._memory = SessionMemory()
        self._executor = get_executor(executor_backend, mcp_host, self._memory)

    def create(self, request: CreateAgentRequest) -> tuple[AgentConfig, Path]:
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        config = AgentConfig(
            agent_id=agent_id,
            name=request.name,
            usecase_context=request.usecase_context,
            selected_mcp_ids=request.selected_mcp_ids,
            selected_plugin_ids=request.selected_plugin_ids,
            model_name=request.model_name,
            max_iterations=request.max_iterations,
            temperature=request.temperature,
        )

        # Start selected registry MCPs
        if request.selected_mcp_ids:
            results = self._mcp.start(request.selected_mcp_ids)
            failed = [sid for sid, ok in results.items() if not ok]
            if failed:
                logger.warning("Some MCPs failed to start: %s", failed)

        # Start MCP-type plugins by injecting them into the host
        if request.selected_plugin_ids:
            try:
                from plugins.plugin_registry import get_plugin_registry
                from plugins.plugin_schema import PluginType
                from mcp.host import ServerConfig
                reg = get_plugin_registry()
                mcp_plugins = [
                    p for p in reg.by_ids(request.selected_plugin_ids)
                    if p.type == PluginType.MCP
                ]
                for plugin in mcp_plugins:
                    cfg_dict = plugin.to_server_config_dict()
                    srv_cfg = ServerConfig(**cfg_dict)
                    # Inject into host registry and start
                    self._mcp.registry.selectable_servers.append(srv_cfg)
                    results = self._mcp.start([srv_cfg.id])
                    if not results.get(srv_cfg.id):
                        logger.warning("Plugin MCP '%s' failed to start", srv_cfg.id)
            except Exception as e:
                logger.error("Failed to start plugin MCPs: %s", e)

        # Load registry data for folder writer
        registry_path = Path(__file__).parent.parent / "mcp" / "registry.json"
        registry_data = json.loads(registry_path.read_text())

        # Write fully self-contained agent folder
        folder = write_agent_folder(config, registry_data)

        self._agents[agent_id] = config
        logger.info("Created agent: %s (%s) → %s", agent_id, request.name, folder)
        return config, folder

    def get(self, agent_id: str) -> AgentConfig | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[dict]:
        return [
            {
                "agent_id": c.agent_id,
                "name": c.name,
                "model": c.model_name,
                "usecase": c.usecase_context[:80],
                "mcps": c.selected_mcp_ids,
                "plugins": c.selected_plugin_ids,
                "folder": str(Path(__file__).parent.parent / "agents" / c.agent_id),
            }
            for c in self._agents.values()
        ]

    def delete(self, agent_id: str) -> bool:
        if agent_id in self._agents:
            self._memory.clear(agent_id)
            del self._agents[agent_id]
            logger.info("Deleted agent: %s", agent_id)
            return True
        return False

    async def run(
        self, agent_id: str, session_id: str, message: str,
        input_type: InputType = InputType.TEXT,
        image_base64: str | None = None,
    ) -> AgentResponse:
        config = self.get(agent_id)
        if not config:
            return AgentResponse(
                session_id=session_id, agent_id=agent_id,
                status=AgentStatus.ERROR, message="Agent not found.",
                error=f"No agent with id '{agent_id}'",
            )
        user_input = UserInput(
            session_id=session_id, agent_id=agent_id,
            input_type=input_type, content=message,
            image_base64=image_base64,
        )
        return await self._executor.run(config, user_input)

    async def stream(self, agent_id: str, session_id: str, message: str):
        config = self.get(agent_id)
        if not config:
            yield "Error: agent not found."
            return
        user_input = UserInput(session_id=session_id, agent_id=agent_id, content=message)
        async for chunk in self._executor.stream(config, user_input):
            yield chunk

    def clear_session(self, session_id: str):
        self._memory.clear(session_id)