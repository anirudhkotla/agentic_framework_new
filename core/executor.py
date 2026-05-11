"""
core/executor.py
----------------
Agent Executor using Mistral via langchain-mistralai.

KEY FIX: Tool args are passed directly from tc["args"] in _execute_tool,
not through the StructuredTool function signature. The StructuredTool
functions exist only to give LangChain schema information — actual
execution goes straight to MCPHost.call_tool() or plugin_executor.
This avoids the kwargs-wrapping bug entirely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langchain_mistralai import ChatMistralAI
from pydantic import create_model

from core.models import (
    AgentConfig, AgentResponse, AgentStatus,
    ChatMessage, MessageRole, ToolCall, UserInput,
)
from mcp.host import MCPHost
from prompts.system_prompt import PromptBuilder

load_dotenv(override=False)

logger = logging.getLogger(__name__)

_RATE_LIMIT_MAX_RETRIES = 4
_RATE_LIMIT_BASE_DELAY  = 2.0
_LLM_TIMEOUT_SECONDS    = 120.0


# ─── Agent memory ─────────────────────────────────────────────────────────────

class SessionMemory:
    def __init__(self, agents_dir: Path | None = None):
        self._agents_dir = agents_dir or Path(__file__).parent.parent / "agents"
        self._store: dict[str, dict[str, Any]] = {}

    def initialize_agent(self, agent_id: str, agent_name: str | None = None):
        data = self._load(agent_id)
        if agent_name and data.get("agent_name") != agent_name:
            data["agent_name"] = agent_name
            self._save(agent_id)
        else:
            self._ensure_file(agent_id)

    def append(self, agent_id: str, session_id: str, message: ChatMessage):
        data = self._load(agent_id)
        sessions = data.setdefault("sessions", {})
        session = sessions.setdefault(session_id, {"session_id": session_id, "messages": []})
        session["messages"].append(message.model_dump(mode="json"))
        data["updated_at"] = message.timestamp.isoformat()
        self._save(agent_id)

    def get(self, agent_id: str, session_id: str | None = None) -> list[ChatMessage]:
        data = self._load(agent_id)
        sessions = data.get("sessions", {})
        raw_messages: list[dict[str, Any]] = []

        if session_id is not None:
            raw_messages = sessions.get(session_id, {}).get("messages", [])
        else:
            for session in sessions.values():
                raw_messages.extend(session.get("messages", []))
            raw_messages.sort(key=lambda msg: msg.get("timestamp", ""))

        return [ChatMessage(**msg) for msg in raw_messages]

    def tree(self, agent_id: str) -> dict[str, Any]:
        return self._load(agent_id)

    def clear(self, agent_id: str, session_id: str | None = None):
        data = self._load(agent_id)
        if session_id is None:
            data["sessions"] = {}
        else:
            data.setdefault("sessions", {}).pop(session_id, None)
        data["updated_at"] = None
        self._save(agent_id)

    def delete(self, agent_id: str):
        self._store.pop(agent_id, None)
        path = self._path(agent_id)
        if path.exists():
            path.unlink()

    def to_lc_messages(self, agent_id: str) -> list:
        out = []
        for msg in self.get(agent_id):
            if msg.role == MessageRole.USER:
                out.append(HumanMessage(content=msg.content))
            elif msg.role == MessageRole.ASSISTANT:
                out.append(AIMessage(content=msg.content))
        return out

    def _path(self, agent_id: str) -> Path:
        return self._agents_dir / agent_id / "memory.json"

    def _default_tree(self, agent_id: str) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "agent_name": None,
            "schema_version": 1,
            "updated_at": None,
            "sessions": {},
        }

    def _load(self, agent_id: str) -> dict[str, Any]:
        if agent_id in self._store:
            return self._store[agent_id]

        path = self._path(agent_id)
        if path.exists():
            try:
                self._store[agent_id] = json.loads(path.read_text())
                return self._store[agent_id]
            except Exception:
                logger.warning("Could not read memory file for %s; starting fresh", agent_id)

        self._store[agent_id] = self._default_tree(agent_id)
        self._save(agent_id)
        return self._store[agent_id]

    def _ensure_file(self, agent_id: str):
        path = self._path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(json.dumps(self._store[agent_id], indent=2))

    def _save(self, agent_id: str):
        path = self._path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._store[agent_id], indent=2, default=str))


# ─── Abstract base ────────────────────────────────────────────────────────────

class BaseExecutor(ABC):
    @abstractmethod
    async def run(self, config: AgentConfig, user_input: UserInput) -> AgentResponse: ...
    @abstractmethod
    async def stream(self, config: AgentConfig, user_input: UserInput) -> AsyncIterator[str]: ...


# ─── Rate limit backoff ───────────────────────────────────────────────────────

async def _with_backoff(coro_fn, max_retries: int = _RATE_LIMIT_MAX_RETRIES):
    delay = _RATE_LIMIT_BASE_DELAY
    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(coro_fn(), timeout=_LLM_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"LLM call timed out after {_LLM_TIMEOUT_SECONDS}s. "
                "Mistral API may be overloaded — try again shortly."
            )
        except Exception as e:
            is_rate = "429" in str(e) or "rate_limit" in str(e).lower()
            if is_rate and attempt < max_retries:
                wait = delay + random.uniform(0, delay * 0.3)
                logger.warning(
                    "Rate limit (attempt %d/%d). Waiting %.1fs",
                    attempt + 1, max_retries, wait,
                )
                await asyncio.sleep(wait)
                delay *= 2
            else:
                raise


# ─── Tool builder helpers ─────────────────────────────────────────────────────

def _schema_to_pydantic(name: str, schema: dict):
    """
    Convert a JSON Schema dict into a Pydantic model for StructuredTool.
    This gives LangChain the correct field names so the LLM sends
    the right argument names (e.g. 'url' not 'query').
    """
    from typing import Optional
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields = {}
    for field_name, field_schema in props.items():
        python_type = _json_type_to_python(field_schema.get("type", "string"))
        if field_name in required:
            fields[field_name] = (python_type, ...)
        else:
            default = field_schema.get("default", None)
            fields[field_name] = (Optional[python_type], default)

    if not fields:
        # Fallback: single 'query' string field
        fields = {"query": (str, ...)}

    return create_model(f"{name}_args", **fields)


def _json_type_to_python(json_type: str):
    return {
        "string":  str,
        "integer": int,
        "number":  float,
        "boolean": bool,
        "array":   list,
        "object":  dict,
    }.get(json_type, str)


def _make_mcp_tool(
    lc_name: str,
    description: str,
    input_schema: dict,
    server_id: str,
    real_name: str,
    mcp_host: MCPHost,
) -> StructuredTool:
    """
    Build a StructuredTool for an MCP tool.

    The function body is a NO-OP placeholder — actual execution happens
    in _execute_tool() which calls mcp_host.call_tool() directly with
    tc["args"] from the LLM response.

    The Pydantic args model is what matters: it tells LangChain what
    field names to use, so the LLM sends {"url": "..."} not {"query": "..."}.
    """
    args_model = _schema_to_pydantic(lc_name, input_schema)

    async def _placeholder(**kwargs) -> str:
        # Called by LangChain test/validation only.
        # Real execution goes through _execute_tool → mcp_host.call_tool
        return f"[{lc_name} placeholder]"

    return StructuredTool(
        name=lc_name,
        description=description,
        args_schema=args_model,
        coroutine=_placeholder,
    )


def _make_plugin_tool(
    lc_name: str,
    description: str,
    input_schema: dict,
    plugin_obj: Any,
) -> StructuredTool:
    """
    Build a StructuredTool for a plugin (http_tool or python_skill).
    Same pattern: args_schema drives what the LLM sends, execution is in _execute_tool.
    """
    args_model = _schema_to_pydantic(lc_name, input_schema)

    async def _placeholder(**kwargs) -> str:
        return f"[{lc_name} plugin placeholder]"

    return StructuredTool(
        name=lc_name,
        description=description,
        args_schema=args_model,
        coroutine=_placeholder,
    )


# ─── Mistral executor ─────────────────────────────────────────────────────────

class MistralExecutor(BaseExecutor):
    def __init__(self, mcp_host: MCPHost, memory: SessionMemory | None = None):
        self._mcp = mcp_host
        self._memory = memory or SessionMemory()
        self._prompt_builder = PromptBuilder()
        # Maps lc_name → (source, server_id, real_name, plugin_obj|None)
        self._tool_registry: dict[str, tuple[str, str, str, Any]] = {}

    def _build_llm(self, config: AgentConfig) -> ChatMistralAI:
        return ChatMistralAI(
            model=config.model_name,
            temperature=config.temperature,
            mistral_api_key=os.environ.get("MISTRAL_API_KEY", ""),
        )

    def _build_tools(self, config: AgentConfig) -> list[StructuredTool]:
        """
        Build StructuredTools for all active MCP tools and plugin tools.

        Each tool's args_schema is built from the real MCP input schema
        so the LLM sends correctly named arguments. The function body
        is a placeholder — execution goes through _execute_tool directly.
        """
        tools = []
        self._tool_registry.clear()

        # ── 1. MCP tools ──────────────────────────────────────────────────────
        for td in self._mcp.get_tool_descriptions():
            if td["server_id"] == "builtin":
                continue

            lc_name = td["lc_name"].replace("-", "_")
            server_id = td["server_id"]
            real_name = td["tool_name"]
            input_schema = td.get("input_schema") or {}
            self._tool_registry[lc_name] = ("mcp", server_id, real_name, None)

            tools.append(_make_mcp_tool(
                lc_name=lc_name,
                description=td["description"],
                input_schema=input_schema,
                server_id=server_id,
                real_name=real_name,
                mcp_host=self._mcp,
            ))

        # ── 2. Plugin tools (http_tool + python_skill) ────────────────────────
        plugin_ids = getattr(config, "selected_plugin_ids", []) or []
        if plugin_ids:
            try:
                from plugins.plugin_registry import get_plugin_registry
                reg = get_plugin_registry()
                for td in reg.get_tool_descriptions(plugin_ids):
                    lc_name = td["lc_name"].replace("-", "_")
                    plugin_obj = reg.get(td["tool_name"])
                    input_schema = td.get("input_schema") or {}
                    self._tool_registry[lc_name] = (
                        "plugin", td["server_id"], td["tool_name"], plugin_obj
                    )
                    tools.append(_make_plugin_tool(
                        lc_name=lc_name,
                        description=td["description"],
                        input_schema=input_schema,
                        plugin_obj=plugin_obj,
                    ))
            except Exception as e:
                logger.error("Failed to load plugin tools: %s", e)

        return tools

    def _build_system_prompt(self, config: AgentConfig) -> str:
        tool_descs = list(self._mcp.get_tool_descriptions())
        plugin_ids = getattr(config, "selected_plugin_ids", []) or []
        if plugin_ids:
            try:
                from plugins.plugin_registry import get_plugin_registry
                tool_descs += get_plugin_registry().get_tool_descriptions(plugin_ids)
            except Exception:
                pass
        return self._prompt_builder.build(
            tool_descriptions=tool_descs,
            usecase_context=config.usecase_context,
        )

    async def run(self, config: AgentConfig, user_input: UserInput) -> AgentResponse:
        start = time.time() * 1000
        tool_calls_log: list[ToolCall] = []

        try:
            llm    = self._build_llm(config)
            tools  = self._build_tools(config)
            system = self._build_system_prompt(config)
            llm_with_tools = llm.bind_tools(tools) if tools else llm

            history  = self._memory.to_lc_messages(config.agent_id) if config.memory_enabled else []
            messages = [
                SystemMessage(content=system),
                *history,
                HumanMessage(content=user_input.content),
            ]
            if config.memory_enabled:
                self._memory.append(
                    config.agent_id,
                    user_input.session_id,
                    ChatMessage(role=MessageRole.USER, content=user_input.content),
                )

            iterations    = 0
            final_content = ""

            while iterations < config.max_iterations:
                iterations += 1
                _msgs = messages[:]
                _llm  = llm_with_tools

                async def _call(_m=_msgs, _l=_llm):
                    return await _l.ainvoke(
                        _m, config=RunnableConfig(tags=[config.agent_id])
                    )

                response = await _with_backoff(_call)

                if not getattr(response, "tool_calls", None):
                    final_content = response.content
                    break

                messages.append(response)
                for tc in response.tool_calls:
                    result, log = await self._execute_tool(tc)
                    tool_calls_log.append(log)
                    messages.append(
                        ToolMessage(content=_to_str(result), tool_call_id=tc["id"])
                    )
            else:
                final_content = (
                    f"Reached max iterations ({config.max_iterations}). "
                    "Partial result returned."
                )
                logger.warning("Agent %s hit max_iterations", config.agent_id)

            if config.memory_enabled:
                self._memory.append(
                    config.agent_id,
                    user_input.session_id,
                    ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content=final_content,
                        metadata={
                            "tool_calls": [
                                call.model_dump(mode="json") for call in tool_calls_log
                            ],
                            "iterations_used": iterations,
                            "duration_ms": round(time.time() * 1000 - start, 2),
                            "status": AgentStatus.COMPLETE.value,
                        },
                    ),
                )
            return AgentResponse(
                session_id=user_input.session_id,
                agent_id=config.agent_id,
                status=AgentStatus.COMPLETE,
                message=final_content,
                tool_calls=tool_calls_log,
                iterations_used=iterations,
                duration_ms=round(time.time() * 1000 - start, 2),
            )

        except TimeoutError as e:
            return AgentResponse(
                session_id=user_input.session_id, agent_id=config.agent_id,
                status=AgentStatus.ERROR, message=str(e), error=str(e),
                tool_calls=tool_calls_log,
                duration_ms=round(time.time() * 1000 - start, 2),
            )
        except Exception as e:
            logger.exception("Executor error [%s]", config.agent_id)
            return AgentResponse(
                session_id=user_input.session_id, agent_id=config.agent_id,
                status=AgentStatus.ERROR, message=f"Error: {e}", error=str(e),
                tool_calls=tool_calls_log,
                duration_ms=round(time.time() * 1000 - start, 2),
            )

    async def stream(self, config: AgentConfig, user_input: UserInput) -> AsyncIterator[str]:
        llm = self._build_llm(config)
        messages = [
            SystemMessage(content=self._build_system_prompt(config)),
            *(self._memory.to_lc_messages(config.agent_id) if config.memory_enabled else []),
            HumanMessage(content=user_input.content),
        ]
        if config.memory_enabled:
            self._memory.append(
                config.agent_id,
                user_input.session_id,
                ChatMessage(role=MessageRole.USER, content=user_input.content),
            )
        final_content = ""
        async for chunk in llm.astream(messages):
            if chunk.content:
                final_content += chunk.content
                yield chunk.content
        if config.memory_enabled and final_content:
            self._memory.append(
                config.agent_id,
                user_input.session_id,
                ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=final_content,
                    metadata={"status": AgentStatus.COMPLETE.value},
                ),
            )

    async def _execute_tool(self, tc: dict) -> tuple[Any, ToolCall]:
        """
        Execute a tool call from the LLM.

        tc["args"] contains the arguments exactly as the LLM sent them
        (correct field names, correct types) because args_schema told it
        what fields to use. We pass them directly to MCPHost or plugin_executor
        — no wrapping, no transformation.
        """
        t0      = time.time() * 1000
        lc_name = tc.get("name", "")
        args    = tc.get("args", {})   # ← direct from LLM, field names match MCP schema

        resolved = self._tool_registry.get(lc_name)
        if resolved:
            source, server_id, real_name, plugin_obj = resolved
        else:
            source     = "mcp"
            server_id  = lc_name.split("__")[0].replace("_", "-")
            real_name  = lc_name
            plugin_obj = None
            logger.warning(
                "Tool '%s' not in registry, fallback server='%s'",
                lc_name, server_id,
            )

        try:
            if source == "plugin" and plugin_obj is not None:
                from plugins.plugin_executor import execute_plugin_tool
                result = await execute_plugin_tool(plugin_obj, args)
            else:
                # Pass args directly — field names match the MCP tool schema
                result = await self._mcp.call_tool(server_id, real_name, args)

            return result, ToolCall(
                server_id=server_id, tool_name=real_name, arguments=args,
                result=result, duration_ms=round(time.time() * 1000 - t0, 2),
            )
        except Exception as e:
            logger.error("Tool call failed [%s/%s]: %s", server_id, real_name, e)
            return {"error": str(e)}, ToolCall(
                server_id=server_id, tool_name=real_name, arguments=args,
                error=str(e), duration_ms=round(time.time() * 1000 - t0, 2),
            )


def _to_str(obj: Any) -> str:
    if isinstance(obj, (dict, list)):
        try:
            return json.dumps(obj, indent=2)
        except Exception:
            pass
    return str(obj)


# ─── Executor registry ────────────────────────────────────────────────────────

_EXECUTORS: dict[str, type[BaseExecutor]] = {
    "mistral": MistralExecutor,
}


def get_executor(
    name: str,
    mcp_host: MCPHost,
    memory: SessionMemory | None = None,
) -> BaseExecutor:
    cls = _EXECUTORS.get(name)
    if not cls:
        raise ValueError(f"Unknown executor '{name}'. Available: {list(_EXECUTORS)}")
    return cls(mcp_host=mcp_host, memory=memory)
