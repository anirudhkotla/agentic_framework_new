"""
core/agent_manager.py
---------------------
Registry of live agent instances.
On creation, calls agent_folder.write_agent_folder() to write
a fully self-contained folder to agents/{agent_id}/.
"""

from __future__ import annotations

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
            model_name=request.model_name,
            max_iterations=request.max_iterations,
            temperature=request.temperature,
        )
        # Start selected MCPs in the framework host
        if request.selected_mcp_ids:
            results = self._mcp.start(request.selected_mcp_ids)
            failed = [sid for sid, ok in results.items() if not ok]
            if failed:
                logger.warning("Some MCPs failed to start: %s", failed)

        # Write fully self-contained agent folder
        registry = load_registry()
        import json
        from pathlib import Path as P
        registry_path = P(__file__).parent.parent / "mcp" / "registry.json"
        registry_data = json.loads(registry_path.read_text())
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
                "folder": str(
                    Path(__file__).parent.parent / "agents" / c.agent_id
                ),
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
                status=AgentStatus.ERROR,
                message="Agent not found.",
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
