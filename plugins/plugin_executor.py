"""
plugins/plugin_executor.py
--------------------------
Executes http_tool and python_skill plugin calls at runtime.

MCPHost handles mcp-type plugins (they run as subprocesses).
This module handles the other two types which run in-process.

The executor calls this when it sees a tool whose server_id starts with "plugin__".
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from plugins.plugin_schema import Plugin, PluginType

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).parent


async def execute_plugin_tool(plugin: Plugin, arguments: dict[str, Any]) -> Any:
    """
    Route a tool call to the correct plugin executor based on plugin type.
    Called by the agent executor when tool server_id starts with 'plugin__'.
    """
    if plugin.type == PluginType.HTTP_TOOL:
        return await _execute_http_tool(plugin, arguments)
    elif plugin.type == PluginType.PYTHON_SKILL:
        return await _execute_python_skill(plugin, arguments)
    else:
        raise ValueError(f"Plugin type '{plugin.type}' should be handled by MCPHost, not plugin_executor")


# ─── HTTP tool executor ───────────────────────────────────────────────────────

async def _execute_http_tool(plugin: Plugin, arguments: dict[str, Any]) -> Any:
    """
    Execute an HTTP tool plugin call.
    Finds the matching endpoint, builds the request, returns the response.
    """
    if not plugin.base_url:
        raise ValueError(f"Plugin '{plugin.id}' has no base_url")
    if not plugin.endpoints:
        raise ValueError(f"Plugin '{plugin.id}' has no endpoints defined")

    # Find endpoint by name or use first one
    tool_name = arguments.get("_endpoint") or plugin.endpoints[0].name
    endpoint = next(
        (e for e in plugin.endpoints if e.name == tool_name),
        plugin.endpoints[0],
    )

    # Build headers
    headers = {"Content-Type": "application/json"}
    if plugin.auth_env:
        token = os.environ.get(plugin.auth_env, "")
        if token:
            headers[plugin.auth_header] = f"Bearer {token}"
        else:
            logger.warning("Plugin '%s': auth env var %s not set", plugin.id, plugin.auth_env)

    # Build URL and params
    url = plugin.base_url.rstrip("/") + endpoint.path
    params = {**endpoint.params}
    body = None

    # Arguments that aren't internal meta go into params/body
    user_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
    if endpoint.method.upper() in ("GET", "DELETE"):
        params.update(user_args)
    else:
        body = user_args or None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method=endpoint.method.upper(),
                url=url,
                headers=headers,
                params=params if params else None,
                json=body,
            )
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return resp.text
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"HTTP tool '{plugin.id}' returned {e.response.status_code}: {e.response.text}"
        )
    except Exception as e:
        raise RuntimeError(f"HTTP tool '{plugin.id}' failed: {e}")


# ─── Python skill executor ────────────────────────────────────────────────────

async def _execute_python_skill(plugin: Plugin, arguments: dict[str, Any]) -> Any:
    """
    Execute a Python skill plugin.
    Loads the module dynamically and calls the specified function.

    The skill function should accept **kwargs and return a string or dict.
    It can be sync or async.
    """
    if not plugin.module_path or not plugin.function:
        raise ValueError(f"Plugin '{plugin.id}' missing module_path or function")

    # Resolve path relative to plugins/user/
    module_file = _PLUGINS_DIR / "user" / plugin.module_path
    if not module_file.exists():
        # Try relative to plugins root
        module_file = _PLUGINS_DIR / plugin.module_path
    if not module_file.exists():
        raise FileNotFoundError(
            f"Python skill module not found: {plugin.module_path}"
        )

    # Load module dynamically
    spec = importlib.util.spec_from_file_location(
        f"plugin_{plugin.id}", module_file
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Get function
    fn = getattr(module, plugin.function, None)
    if fn is None:
        raise AttributeError(
            f"Function '{plugin.function}' not found in {module_file}"
        )

    # Call — support both sync and async
    import asyncio
    import inspect
    if inspect.iscoroutinefunction(fn):
        result = await fn(**arguments)
    else:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: fn(**arguments))

    return result