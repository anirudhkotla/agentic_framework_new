"""
plugins/plugin_schema.py
------------------------
Defines the standard .plugin.json file format and validation.

Every plugin — whether community-built or user-uploaded — must conform
to this schema. The PluginLoader reads, validates, and converts plugins
into ServerConfig objects that MCPHost can launch exactly like registry MCPs.

Plugin types:
  mcp         → subprocess MCP server (stdio JSON-RPC, same as registry)
  http_tool   → REST API endpoint wrapped as a tool
  python_skill→ local Python function exposed as a tool

Standard .plugin.json file:
{
  "id":          "my_crm",           // unique, alphanumeric + hyphens
  "name":        "Internal CRM",     // display name
  "version":     "1.0.0",
  "type":        "mcp",              // mcp | http_tool | python_skill
  "category":    "business",         // web|dev|database|productivity|business|devops|ai_ml|custom
  "description": "Access internal CRM contacts and deals",
  "author":      "acme-corp",        // optional
  "tags":        ["crm", "sales"],   // optional, for search

  // ── For type: mcp ──────────────────────────────────────────────
  "command":     "npx",
  "args":        ["-y", "my-crm-mcp"],
  "env":         {"CRM_API_KEY": "${CRM_API_KEY}"},
  "required_env":["CRM_API_KEY"],

  // ── For type: http_tool ────────────────────────────────────────
  "base_url":    "https://api.mycrm.com/v1",
  "auth_header": "Authorization",
  "auth_env":    "CRM_API_KEY",
  "endpoints": [
    {
      "name":        "list_contacts",
      "method":      "GET",
      "path":        "/contacts",
      "description": "List all CRM contacts",
      "params":      {"limit": 50}
    }
  ],

  // ── For type: python_skill ─────────────────────────────────────
  "module_path": "skills/my_skill.py",
  "function":    "run_skill",
  "input_schema": {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"]
  }
}
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parent


class PluginType(str, Enum):
    MCP          = "mcp"
    HTTP_TOOL    = "http_tool"
    PYTHON_SKILL = "python_skill"


class HttpEndpoint(BaseModel):
    name: str
    method: str = "GET"
    path: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    body_schema: dict[str, Any] = Field(default_factory=dict)


class Plugin(BaseModel):
    """Single source of truth for what a valid plugin looks like."""

    id: str
    name: str
    version: str = "1.0.0"
    type: PluginType
    category: str = "custom"
    description: str
    author: str = "unknown"
    tags: list[str] = Field(default_factory=list)
    source: str = "user"   # "user" | "community"

    # ── MCP fields ──────────────────────────────────────────────────────────
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    required_env: list[str] = Field(default_factory=list)

    # ── HTTP tool fields ─────────────────────────────────────────────────────
    base_url: str | None = None
    auth_header: str = "Authorization"
    auth_env: str | None = None
    endpoints: list[HttpEndpoint] = Field(default_factory=list)

    # ── Python skill fields ──────────────────────────────────────────────────
    module_path: str | None = None
    function: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-_]*$", v):
            raise ValueError(
                f"Plugin id '{v}' must be lowercase alphanumeric with hyphens/underscores"
            )
        return v

    def to_server_config_dict(self) -> dict:
        """
        Convert this plugin into a dict compatible with MCPHost.ServerConfig.
        Only valid for MCP-type plugins.
        """
        if self.type != PluginType.MCP:
            raise ValueError(f"Only mcp-type plugins convert to ServerConfig (got {self.type})")
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "enabled": False,
            "description": self.description,
            "required_env": self.required_env,
        }

    def to_tool_description(self) -> dict:
        """
        Convert to a tool description dict compatible with get_tool_descriptions().
        Used for http_tool and python_skill types.
        """
        lc_name = self.id.replace("-", "_")
        return {
            "server_id": f"plugin__{self.id}",
            "tool_name": self.id,
            "lc_name": lc_name,
            "description": self.description,
            "category": self.category,
            "input_schema": self.input_schema or {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "plugin_type": self.type.value,
            "plugin": self.model_dump(),
        }


def load_plugin_from_file(path: Path) -> Plugin | None:
    """Load and validate a .plugin.json file. Returns None on failure."""
    try:
        raw = json.loads(path.read_text())
        plugin = Plugin(**raw)
        logger.info("Loaded plugin: %s (%s) from %s", plugin.name, plugin.type.value, path.name)
        return plugin
    except Exception as e:
        logger.error("Failed to load plugin from %s: %s", path, e)
        return None


def validate_plugin_json(raw: dict) -> tuple[bool, str]:
    """
    Validate a raw plugin dict.
    Returns (is_valid, error_message).
    Used by the upload API before saving.
    """
    try:
        Plugin(**raw)
        return True, ""
    except Exception as e:
        return False, str(e)