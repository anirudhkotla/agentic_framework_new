"""
plugins/plugin_registry.py
--------------------------
Central registry for all plugins (community + user-uploaded).

Responsibilities:
- Scan community/ and user/ folders on startup
- Watch for new uploads (re-scan on demand)
- Provide unified list to the UI and agent builder
- Convert MCP plugins into ServerConfig for MCPHost
- Convert http_tool / python_skill plugins into tool descriptions for executor

This is intentionally separate from mcp/registry.json — plugins are
user-defined extensions, not framework-defined defaults.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from plugins.plugin_schema import Plugin, PluginType, load_plugin_from_file, validate_plugin_json

logger = logging.getLogger(__name__)

_PLUGINS_DIR   = Path(__file__).parent
_COMMUNITY_DIR = _PLUGINS_DIR / "community"
_USER_DIR      = _PLUGINS_DIR / "user"


class PluginRegistry:
    """
    Singleton-style registry for all installed plugins.
    Call .reload() to re-scan disk after a new upload.
    """

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        self.reload()

    # ── Load / reload ─────────────────────────────────────────────────────────

    def reload(self):
        """Scan community/ and user/ dirs and reload all plugins."""
        self._plugins.clear()
        for folder, source in [(_COMMUNITY_DIR, "community"), (_USER_DIR, "user")]:
            folder.mkdir(exist_ok=True)
            for path in sorted(folder.glob("*.plugin.json")):
                plugin = load_plugin_from_file(path)
                if plugin:
                    plugin.source = source
                    if plugin.id in self._plugins:
                        logger.warning(
                            "Duplicate plugin id '%s' — user plugin overrides community",
                            plugin.id,
                        )
                    self._plugins[plugin.id] = plugin
        logger.info("Plugin registry loaded: %d plugin(s)", len(self._plugins))

    # ── Query ─────────────────────────────────────────────────────────────────

    def all(self) -> list[Plugin]:
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> Plugin | None:
        return self._plugins.get(plugin_id)

    def by_type(self, ptype: PluginType) -> list[Plugin]:
        return [p for p in self._plugins.values() if p.type == ptype]

    def by_ids(self, ids: list[str]) -> list[Plugin]:
        return [self._plugins[pid] for pid in ids if pid in self._plugins]

    def search(self, query: str) -> list[Plugin]:
        """Search plugins by name, description, tags, category, id."""
        q = query.lower()
        return [
            p for p in self._plugins.values()
            if (
                q in p.id.lower()
                or q in p.name.lower()
                or q in p.description.lower()
                or q in p.category.lower()
                or any(q in t.lower() for t in p.tags)
            )
        ]

    # ── Upload ────────────────────────────────────────────────────────────────

    def install(self, raw: dict) -> tuple[bool, str]:
        """
        Validate and install a plugin from a raw dict (from file upload).
        Returns (success, message).
        """
        valid, err = validate_plugin_json(raw)
        if not valid:
            return False, f"Invalid plugin format: {err}"

        from plugins.plugin_schema import Plugin
        plugin = Plugin(**raw)
        dest = _USER_DIR / f"{plugin.id}.plugin.json"

        if dest.exists():
            dest.write_text(json.dumps(raw, indent=2))
            self.reload()
            return True, f"Plugin '{plugin.name}' updated."

        dest.write_text(json.dumps(raw, indent=2))
        self.reload()
        return True, f"Plugin '{plugin.name}' installed."

    def uninstall(self, plugin_id: str) -> tuple[bool, str]:
        """Remove a user plugin. Community plugins cannot be removed."""
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            return False, f"Plugin '{plugin_id}' not found."
        if plugin.source == "community":
            return False, "Community plugins cannot be removed."
        path = _USER_DIR / f"{plugin_id}.plugin.json"
        if path.exists():
            path.unlink()
        self.reload()
        return True, f"Plugin '{plugin_id}' removed."

    # ── MCPHost integration ───────────────────────────────────────────────────

    def get_mcp_server_configs(self, plugin_ids: list[str]) -> list[dict]:
        """
        Return ServerConfig-compatible dicts for all selected MCP-type plugins.
        Used by MCPHost.start() to launch plugin MCP servers.
        """
        configs = []
        for pid in plugin_ids:
            plugin = self._plugins.get(pid)
            if plugin and plugin.type == PluginType.MCP:
                try:
                    configs.append(plugin.to_server_config_dict())
                except Exception as e:
                    logger.error("Cannot convert plugin '%s' to ServerConfig: %s", pid, e)
        return configs

    def get_tool_descriptions(self, plugin_ids: list[str]) -> list[dict]:
        """
        Return tool description dicts for http_tool and python_skill plugins.
        These go straight into the executor tool registry (no subprocess needed).
        """
        descs = []
        for pid in plugin_ids:
            plugin = self._plugins.get(pid)
            if plugin and plugin.type in (PluginType.HTTP_TOOL, PluginType.PYTHON_SKILL):
                descs.append(plugin.to_tool_description())
        return descs

    # ── Serialization (for API / UI) ──────────────────────────────────────────

    def to_api_list(self) -> list[dict]:
        """Return all plugins as dicts for the API response."""
        return [
            {
                "id": p.id,
                "name": p.name,
                "version": p.version,
                "type": p.type.value,
                "category": p.category,
                "description": p.description,
                "author": p.author,
                "tags": p.tags,
                "source": p.source,
                "required_env": p.required_env,
            }
            for p in self._plugins.values()
        ]


# ── Module-level singleton ────────────────────────────────────────────────────
# Import this in routes.py and streamlit_app.py

_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry