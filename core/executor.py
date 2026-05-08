"""
core/executor.py
----------------
Agent Executor using Mistral via langchain-mistralai.

- Full agentic tool-call loop (LLM → tools → LLM)
- Tool registry: lc_name → (server_id, real_tool_name)
- 429 rate limit exponential backoff with jitter
- Session memory injected per turn
- Abstract BaseExecutor — swap LLMs by subclassing
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_core.runnables import RunnableConfig
from langchain_mistralai import ChatMistralAI

from core.models import (
    AgentConfig, AgentResponse, AgentStatus,
    ChatMessage, MessageRole, ToolCall, UserInput,
)
from mcp.host import MCPHost
from prompts.system_prompt import PromptBuilder

load_dotenv(override=False)

logger = logging.getLogger(__name__)

_RATE_LIMIT_MAX_RETRIES = 4
_RATE_LIMIT_BASE_DELAY = 2.0
_LLM_TIMEOUT_SECONDS = 120.0


# ─── Session memory ───────────────────────────────────────────────────────────

class SessionMemory:
    def __init__(self):
        self._store: dict[str, list[ChatMessage]] = {}

    def append(self, session_id: str, message: ChatMessage):
        self._store.setdefault(session_id, []).append(message)

    def get(self, session_id: str) -> list[ChatMessage]:
        return self._store.get(session_id, [])

    def clear(self, session_id: str):
        self._store.pop(session_id, None)

    def to_lc_messages(self, session_id: str) -> list:
        out = []
        for msg in self.get(session_id):
            if msg.role == MessageRole.USER:
                out.append(HumanMessage(content=msg.content))
            elif msg.role == MessageRole.ASSISTANT:
                out.append(AIMessage(content=msg.content))
        return out


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
            is_rate = "429" in str(e) or "rate_limit" in str(e).lower() or "rate limit" in str(e).lower()
            if is_rate and attempt < max_retries:
                wait = delay + random.uniform(0, delay * 0.3)
                logger.warning("Rate limit (attempt %d/%d). Waiting %.1fs", attempt + 1, max_retries, wait)
                await asyncio.sleep(wait)
                delay *= 2
            else:
                raise


# ─── Mistral executor ─────────────────────────────────────────────────────────

class MistralExecutor(BaseExecutor):
    def __init__(self, mcp_host: MCPHost, memory: SessionMemory | None = None):
        self._mcp = mcp_host
        self._memory = memory or SessionMemory()
        self._prompt_builder = PromptBuilder()
        # Maps lc_name → (server_id, real_tool_name)
        self._tool_registry: dict[str, tuple[str, str]] = {}

    def _build_llm(self, config: AgentConfig) -> ChatMistralAI:
        return ChatMistralAI(
            model=config.model_name,
            temperature=config.temperature,
            mistral_api_key=os.environ.get("MISTRAL_API_KEY", ""),
        )

    def _build_tools(self, config: AgentConfig) -> list[StructuredTool]:
        """
        Build LangChain tools from real MCP tool schemas.
        Registry maps lc_name → (server_id, real_tool_name) for exact resolution.
        """
        tools = []
        self._tool_registry.clear()

        for td in self._mcp.get_tool_descriptions():
            if td["server_id"] == "builtin":
                continue

            lc_name = td["lc_name"].replace("-", "_")
            server_id = td["server_id"]
            real_tool_name = td["tool_name"]
            self._tool_registry[lc_name] = (server_id, real_tool_name)

            schema = td.get("input_schema") or {}
            description = td["description"]

            async def _fn(
                query: str = "",
                _sid: str = server_id,
                _tool: str = real_tool_name,
                _schema: dict = schema,
                **kwargs,
            ) -> str:
                args = kwargs if kwargs else ({"query": query} if query else {})
                try:
                    result = await self._mcp.call_tool(_sid, _tool, args)
                    return _to_str(result)
                except Exception as e:
                    return f"Tool error ({_sid}/{_tool}): {e}"

            tools.append(StructuredTool.from_function(
                coroutine=_fn,
                name=lc_name,
                description=description,
            ))
        return tools

    def _build_system_prompt(self, config: AgentConfig) -> str:
        return self._prompt_builder.build(
            tool_descriptions=self._mcp.get_tool_descriptions(),
            usecase_context=config.usecase_context,
        )

    async def run(self, config: AgentConfig, user_input: UserInput) -> AgentResponse:
        start = time.time() * 1000
        tool_calls_log: list[ToolCall] = []

        try:
            llm = self._build_llm(config)
            tools = self._build_tools(config)
            system_prompt = self._build_system_prompt(config)
            llm_with_tools = llm.bind_tools(tools) if tools else llm

            history = self._memory.to_lc_messages(user_input.session_id)
            messages = [
                SystemMessage(content=system_prompt),
                *history,
                HumanMessage(content=user_input.content),
            ]
            self._memory.append(
                user_input.session_id,
                ChatMessage(role=MessageRole.USER, content=user_input.content),
            )

            iterations = 0
            final_content = ""

            while iterations < config.max_iterations:
                iterations += 1

                _msgs = messages[:]
                _llm = llm_with_tools

                async def _call(_m=_msgs, _l=_llm):
                    return await _l.ainvoke(_m, config=RunnableConfig(tags=[config.agent_id]))

                response = await _with_backoff(_call)

                if not getattr(response, "tool_calls", None):
                    final_content = response.content
                    break

                messages.append(response)
                for tc in response.tool_calls:
                    result, log = await self._execute_tool(tc)
                    tool_calls_log.append(log)
                    messages.append(ToolMessage(content=_to_str(result), tool_call_id=tc["id"]))
            else:
                final_content = f"Reached max iterations ({config.max_iterations}). Partial result returned."
                logger.warning("Agent %s hit max_iterations", config.agent_id)

            self._memory.append(
                user_input.session_id,
                ChatMessage(role=MessageRole.ASSISTANT, content=final_content),
            )
            return AgentResponse(
                session_id=user_input.session_id, agent_id=config.agent_id,
                status=AgentStatus.COMPLETE, message=final_content,
                tool_calls=tool_calls_log, iterations_used=iterations,
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
            *self._memory.to_lc_messages(user_input.session_id),
            HumanMessage(content=user_input.content),
        ]
        async for chunk in llm.astream(messages):
            if chunk.content:
                yield chunk.content

    async def _execute_tool(self, tc: dict) -> tuple[Any, ToolCall]:
        t0 = time.time() * 1000
        lc_name = tc.get("name", "")
        args = tc.get("args", {})

        resolved = self._tool_registry.get(lc_name)
        if resolved:
            server_id, real_name = resolved
        else:
            server_id = lc_name.split("__")[0].replace("_", "-")
            real_name = lc_name
            logger.warning("Tool '%s' not in registry, fallback server='%s'", lc_name, server_id)

        try:
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


def get_executor(name: str, mcp_host: MCPHost, memory: SessionMemory | None = None) -> BaseExecutor:
    cls = _EXECUTORS.get(name)
    if not cls:
        raise ValueError(f"Unknown executor '{name}'. Available: {list(_EXECUTORS)}")
    return cls(mcp_host=mcp_host, memory=memory)
