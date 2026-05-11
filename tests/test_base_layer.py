"""
tests/test_base_layer.py — Full test suite for the Constant System base layer.
"""

from __future__ import annotations

import json
import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import MagicMock


# ─── 1. Models ────────────────────────────────────────────────────────────────

class TestModels:

    def test_agent_config_defaults(self):
        from core.models import AgentConfig
        c = AgentConfig(agent_id="test-01", name="T", usecase_context="x")
        assert c.model_name  # reads from env
        assert c.max_iterations == 10
        assert c.temperature == 0.3

    def test_agent_id_lowercased(self):
        from core.models import AgentConfig
        assert AgentConfig(agent_id="TEST-AGENT", name="T", usecase_context="x").agent_id == "test-agent"

    def test_agent_id_invalid(self):
        from core.models import AgentConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentConfig(agent_id="bad id!", name="T", usecase_context="x")

    def test_temperature_bounds(self):
        from core.models import AgentConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentConfig(agent_id="x", name="x", usecase_context="x", temperature=2.0)

    def test_user_input_strips(self):
        from core.models import UserInput
        assert UserInput(session_id="s", agent_id="a", content="  hello  ").content == "hello"

    def test_user_input_empty_rejected(self):
        from core.models import UserInput
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserInput(session_id="s", agent_id="a", content="   ")

    def test_agent_response_defaults(self):
        from core.models import AgentResponse, AgentStatus
        r = AgentResponse(session_id="s", agent_id="a", status=AgentStatus.COMPLETE, message="ok")
        assert r.tool_calls == [] and r.error is None

    def test_create_request_defaults(self):
        from core.models import CreateAgentRequest
        r = CreateAgentRequest(name="A", usecase_context="x")
        assert r.selected_mcp_ids == [] and r.max_iterations == 10

    def test_default_model_is_string(self):
        from core.models import _default_model
        assert isinstance(_default_model(), str) and len(_default_model()) > 0

    def test_health_response(self):
        from core.models import HealthResponse
        h = HealthResponse(status="ok", version="1.0.0", active_agents=1, active_mcp_servers=2)
        assert h.status == "ok"


# ─── 2. MCP Registry ──────────────────────────────────────────────────────────

class TestMCPRegistry:

    def test_loads(self):
        from mcp.host import load_registry
        r = load_registry()
        assert r.version == "1.0.0"
        assert len(r.base_servers) >= 4
        assert len(r.selectable_servers) > 0

    def test_base_enabled(self):
        from mcp.host import load_registry
        for s in load_registry().base_servers:
            assert s.enabled is True

    def test_selectable_disabled(self):
        from mcp.host import load_registry
        for s in load_registry().selectable_servers:
            assert s.enabled is False

    def test_all_have_required_fields(self):
        from mcp.host import load_registry
        r = load_registry()
        for s in r.base_servers + r.selectable_servers:
            assert s.id and s.name and s.command and s.description

    def test_resolve_env_var(self):
        from mcp.host import _resolve
        os.environ["_TEST_XYZ"] = "resolved_value"
        assert _resolve("${_TEST_XYZ}") == "resolved_value"
        del os.environ["_TEST_XYZ"]

    def test_resolve_passthrough(self):
        from mcp.host import _resolve
        assert _resolve("plain") == "plain"

    def test_selectable_ids_present(self):
        from mcp.host import load_registry
        ids = {s.id for s in load_registry().selectable_servers}
        for eid in ["github", "firecrawl", "slack", "jira", "hubspot", "stripe"]:
            assert eid in ids

    def test_builtin_skills(self):
        from mcp.host import load_registry
        skills = load_registry().builtin_skills
        assert len(skills) > 0
        for sk in skills:
            assert sk.enabled is True

    def test_github_required_env(self):
        from mcp.host import load_registry
        gh = next(s for s in load_registry().selectable_servers if s.id == "github")
        assert "GITHUB_TOKEN" in gh.required_env


# ─── 3. Prompt Builder ────────────────────────────────────────────────────────

class TestPromptBuilder:

    def test_no_tools(self):
        from prompts.system_prompt import PromptBuilder
        p = PromptBuilder().build(tool_descriptions=[], usecase_context="Test")
        assert "Test" in p and "No external tools" in p

    def test_usecase_injected(self):
        from prompts.system_prompt import PromptBuilder
        ctx = "You are a Jira specialist."
        assert ctx in PromptBuilder().build(usecase_context=ctx)

    def test_tools_injected(self):
        from prompts.system_prompt import PromptBuilder
        tools = [{"server_id": "github", "tool_name": "list_repos",
                  "lc_name": "github__list_repos", "description": "List repos",
                  "category": "dev", "input_schema": {}}]
        p = PromptBuilder().build(tool_descriptions=tools)
        assert "github__list_repos" in p and "[DEV]" in p

    def test_groups_by_category(self):
        from prompts.system_prompt import PromptBuilder
        tools = [
            {"server_id": "a", "tool_name": "t", "lc_name": "a__t",
             "description": "d", "category": "web", "input_schema": {}},
            {"server_id": "b", "tool_name": "t", "lc_name": "b__t",
             "description": "d", "category": "database", "input_schema": {}},
        ]
        p = PromptBuilder().build(tool_descriptions=tools)
        assert "[WEB]" in p and "[DATABASE]" in p

    def test_safety_present(self):
        from prompts.system_prompt import PromptBuilder
        p = PromptBuilder().build()
        assert "Safety guardrails" in p and "Escalation rules" in p

    def test_custom_template(self):
        from prompts.system_prompt import PromptBuilder
        b = PromptBuilder(template="Ctx: ${USECASE_CONTEXT}. Tools: ${TOOL_LIST}.")
        assert "Custom" in b.build(usecase_context="Custom")


# ─── 4. Session Memory ────────────────────────────────────────────────────────

class TestSessionMemory:

    def test_initializes_agent_memory_file(self, tmp_path):
        from core.executor import SessionMemory
        m = SessionMemory(agents_dir=tmp_path)
        m.initialize_agent("agent-a", "Agent A")
        path = tmp_path / "agent-a" / "memory.json"
        data = json.loads(path.read_text())
        assert data["agent_id"] == "agent-a"
        assert data["agent_name"] == "Agent A"
        assert data["sessions"] == {}

    def test_append_get(self, tmp_path):
        from core.executor import SessionMemory
        from core.models import ChatMessage, MessageRole
        m = SessionMemory(agents_dir=tmp_path)
        m.append("agent-a", "s1", ChatMessage(role=MessageRole.USER, content="Hello"))
        assert m.get("agent-a", "s1")[0].content == "Hello"

    def test_empty(self, tmp_path):
        from core.executor import SessionMemory
        assert SessionMemory(agents_dir=tmp_path).get("agent-x") == []

    def test_clear(self, tmp_path):
        from core.executor import SessionMemory
        from core.models import ChatMessage, MessageRole
        m = SessionMemory(agents_dir=tmp_path)
        m.append("agent-a", "s1", ChatMessage(role=MessageRole.USER, content="hi"))
        m.clear("agent-a", "s1")
        assert m.get("agent-a", "s1") == []

    def test_isolated(self, tmp_path):
        from core.executor import SessionMemory
        from core.models import ChatMessage, MessageRole
        m = SessionMemory(agents_dir=tmp_path)
        m.append("agent-a", "s1", ChatMessage(role=MessageRole.USER, content="A"))
        m.append("agent-b", "s1", ChatMessage(role=MessageRole.USER, content="B"))
        assert m.get("agent-a", "s1")[0].content == "A"
        assert m.get("agent-b", "s1")[0].content == "B"

    def test_tree_keeps_session_branches(self, tmp_path):
        from core.executor import SessionMemory
        from core.models import ChatMessage, MessageRole
        m = SessionMemory(agents_dir=tmp_path)
        m.append("agent-a", "s1", ChatMessage(role=MessageRole.USER, content="A"))
        m.append("agent-a", "s2", ChatMessage(role=MessageRole.USER, content="B"))
        tree = m.tree("agent-a")
        assert set(tree["sessions"]) == {"s1", "s2"}

    def test_to_lc_messages(self, tmp_path):
        from core.executor import SessionMemory
        from core.models import ChatMessage, MessageRole
        from langchain_core.messages import HumanMessage, AIMessage
        m = SessionMemory(agents_dir=tmp_path)
        m.append("agent-a", "s1", ChatMessage(role=MessageRole.USER, content="Q"))
        m.append("agent-a", "s1", ChatMessage(role=MessageRole.ASSISTANT, content="A"))
        lc = m.to_lc_messages("agent-a")
        assert isinstance(lc[0], HumanMessage) and isinstance(lc[1], AIMessage)


# ─── 5. Agent folder writer ───────────────────────────────────────────────────

class TestAgentFolder:

    def _make_config(self, agent_id="agent-testfolder"):
        from core.models import AgentConfig
        return AgentConfig(
            agent_id=agent_id, name="Test Agent",
            usecase_context="Handle GitHub tasks",
            selected_mcp_ids=["github"],
        )

    def _registry_data(self):
        import json
        from pathlib import Path
        return json.loads((Path(__file__).parent.parent / "mcp" / "registry.json").read_text())

    def test_folder_created(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        assert folder.exists()
        shutil.rmtree(folder)

    def test_all_files_present(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        assert (folder / "agent_config.json").exists()
        assert (folder / "memory.json").exists()
        assert (folder / ".env.template").exists()
        assert (folder / "README.md").exists()
        assert (folder / "requirements.txt").exists()
        assert (folder / "main.py").exists()
        assert (folder / "streamlit_ui.py").exists()
        shutil.rmtree(folder)

    def test_source_files_copied(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        assert (folder / "core" / "executor.py").exists()
        assert (folder / "core" / "models.py").exists()
        assert (folder / "mcp" / "host.py").exists()
        assert (folder / "prompts" / "system_prompt.py").exists()
        assert (folder / "api" / "routes.py").exists()
        shutil.rmtree(folder)

    def test_linked_folder_avoids_runtime_copies(self):
        from core.agent_folder import write_linked_agent_folder
        cfg = self._make_config()
        folder = write_linked_agent_folder(cfg, self._registry_data())
        assert (folder / "agent_config.json").exists()
        assert (folder / "mcp" / "registry.json").exists()
        assert (folder / "prompts" / "system_prompt.md").exists()
        assert not (folder / "core" / "executor.py").exists()
        assert not (folder / "api" / "routes.py").exists()
        assert not (folder / ".env.template").exists()
        shutil.rmtree(folder)

    def test_env_template_has_mistral_key(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        env = (folder / ".env.template").read_text()
        assert "MISTRAL_API_KEY" in env
        shutil.rmtree(folder)

    def test_env_template_has_github_token(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        env = (folder / ".env.template").read_text()
        assert "GITHUB_TOKEN" in env  # because github MCP is selected
        shutil.rmtree(folder)

    def test_registry_filtered(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        registry = json.loads((folder / "mcp" / "registry.json").read_text())
        sel_ids = {s["id"] for s in registry["layers"]["selectable"]["servers"]}
        assert sel_ids == {"github"}  # only selected one
        shutil.rmtree(folder)

    def test_agent_config_json(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        loaded = json.loads((folder / "agent_config.json").read_text())
        assert loaded["agent_id"] == "agent-testfolder"
        shutil.rmtree(folder)

    def test_main_py_runnable(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        main_text = (folder / "main.py").read_text()
        assert "run_chat" in main_text
        assert "run_api" in main_text
        assert "MistralExecutor" in main_text
        shutil.rmtree(folder)

    def test_deployed_config_flag(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        folder = write_agent_folder(cfg, self._registry_data())
        loaded = json.loads((folder / "agent_config.json").read_text())
        assert loaded["deployed"] is True
        shutil.rmtree(folder)

    def test_deployed_agent_includes_selected_mcp_plugin(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        cfg.selected_plugin_ids = ["my-mcp"]
        folder = write_agent_folder(cfg, self._registry_data())
        registry = json.loads((folder / "mcp" / "registry.json").read_text())
        sel_ids = {s["id"] for s in registry["layers"]["selectable"]["servers"]}
        main_text = (folder / "main.py").read_text()
        env = (folder / ".env.template").read_text()
        assert "my-mcp" in sel_ids
        assert (folder / "plugins" / "user" / "my-mcp-plugin.plugin.json").exists()
        assert "my-mcp" in main_text
        assert "MY_API_KEY" in env
        shutil.rmtree(folder)

    def test_deployed_agent_copies_selected_python_skill_plugin(self):
        from core.agent_folder import write_agent_folder
        cfg = self._make_config()
        cfg.selected_plugin_ids = ["hi-anirudh"]
        folder = write_agent_folder(cfg, self._registry_data())
        assert (folder / "plugins" / "community" / "hi-anirudh.plugin.json").exists()
        assert (folder / "plugins" / "community" / "skills" / "hi_anirudh.py").exists()
        shutil.rmtree(folder)


# ─── 6. Agent Manager ────────────────────────────────────────────────────────

class TestAgentManager:

    def _manager(self):
        from core.agent_manager import AgentManager
        from mcp.host import MCPHost
        host = MagicMock(spec=MCPHost)
        host.start.return_value = {}
        host.get_tool_descriptions.return_value = []
        host.status.return_value = {"active_servers": [], "builtin_skills": [], "total_active": 0}
        return AgentManager(mcp_host=host)

    def test_create_returns_config_and_folder(self):
        from core.models import CreateAgentRequest
        m = self._manager()
        cfg, folder = m.create(CreateAgentRequest(name="T", usecase_context="x"))
        assert cfg.agent_id.startswith("agent-") and folder.exists()
        assert cfg.deployed is False
        assert not (folder / "core" / "executor.py").exists()
        shutil.rmtree(folder)

    def test_deploy_promotes_agent_folder(self):
        from core.models import CreateAgentRequest
        m = self._manager()
        cfg, folder = m.create(CreateAgentRequest(name="T", usecase_context="x"))
        result = m.deploy(cfg.agent_id)
        assert result is not None
        deployed_cfg, deployed_folder = result
        assert deployed_cfg.deployed is True
        assert deployed_folder == folder
        assert (deployed_folder / "core" / "executor.py").exists()
        assert (deployed_folder / ".env.template").exists()
        shutil.rmtree(deployed_folder)

    def test_get_existing(self):
        from core.models import CreateAgentRequest
        m = self._manager()
        cfg, folder = m.create(CreateAgentRequest(name="T", usecase_context="x"))
        assert m.get(cfg.agent_id) is not None
        shutil.rmtree(folder)

    def test_get_nonexistent(self):
        assert self._manager().get("ghost") is None

    def test_delete(self):
        from core.models import CreateAgentRequest
        m = self._manager()
        cfg, folder = m.create(CreateAgentRequest(name="T", usecase_context="x"))
        assert m.delete(cfg.agent_id) is True
        assert m.get(cfg.agent_id) is None
        if folder.exists():
            shutil.rmtree(folder)

    def test_delete_nonexistent(self):
        assert self._manager().delete("ghost") is False

    @pytest.mark.asyncio
    async def test_run_unknown_agent(self):
        from core.models import AgentStatus
        m = self._manager()
        r = await m.run("ghost", "s1", "hello")
        assert r.status == AgentStatus.ERROR


# ─── 7. API Routes ────────────────────────────────────────────────────────────

class TestAPIRoutes:

    def _client(self):
        from mcp.host import MCPHost
        from core.agent_manager import AgentManager
        from api.routes import create_app
        from fastapi.testclient import TestClient
        host = MagicMock(spec=MCPHost)
        host.status.return_value = {"active_servers": [], "builtin_skills": [], "total_active": 0}
        host.get_tool_descriptions.return_value = []
        mgr = MagicMock(spec=AgentManager)
        mgr.list_agents.return_value = []
        return TestClient(create_app(host, mgr)), host, mgr

    def test_health(self):
        client, _, _ = self._client()
        r = client.get("/api/v1/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

    def test_mcp_status(self):
        client, _, _ = self._client()
        assert client.get("/api/v1/mcp/status").status_code == 200

    def test_list_agents_empty(self):
        client, _, _ = self._client()
        assert client.get("/api/v1/agents").json()["agents"] == []

    def test_get_agent_404(self):
        client, _, mgr = self._client()
        mgr.get.return_value = None
        assert client.get("/api/v1/agents/ghost").status_code == 404

    def test_create_agent(self):
        from core.models import AgentConfig
        client, _, mgr = self._client()
        cfg = AgentConfig(agent_id="agent-abc123", name="T", usecase_context="x")
        from pathlib import Path
        mgr.create.return_value = (cfg, Path("/tmp/agent-abc123"))
        r = client.post("/api/v1/agents", json={"name": "T", "usecase_context": "x"})
        assert r.status_code == 200
        data = r.json()
        assert data["agent_id"] == "agent-abc123"
        assert data["agent_folder"] == "/tmp/agent-abc123"

    def test_deploy_agent(self):
        from core.models import AgentConfig
        client, _, mgr = self._client()
        cfg = AgentConfig(agent_id="agent-abc123", name="T", usecase_context="x", deployed=True)
        from pathlib import Path
        mgr.deploy.return_value = (cfg, Path("/tmp/agent-abc123"))
        r = client.post("/api/v1/agents/agent-abc123/deploy")
        assert r.status_code == 200
        data = r.json()
        assert data["agent_id"] == "agent-abc123"
        assert data["config"]["deployed"] is True

    def test_delete_404(self):
        client, _, mgr = self._client()
        mgr.delete.return_value = False
        assert client.delete("/api/v1/agents/ghost").status_code == 404

    def test_registry_shape(self):
        client, _, _ = self._client()
        r = client.get("/api/v1/mcp/registry")
        assert r.status_code == 200
        data = r.json()
        assert "base" in data and "selectable" in data and "builtin_skills" in data
