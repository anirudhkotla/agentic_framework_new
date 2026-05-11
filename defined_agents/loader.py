"""
defined_agents/loader.py
------------------------
Loads and serves defined agent templates.

Defined agents are pre-configured JSON templates that users instantiate.
They are NOT running agents — they are blueprints. When a user picks one,
an instance is created via AgentManager.create() with the template's
defaults pre-filled but overridable by the user.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFINED_AGENTS_DIR = Path(__file__).parent


def load_all() -> list[dict[str, Any]]:
    """Load all defined agent templates sorted by display order."""
    agents = []
    order = [
        "coding-agent",
        "content-writer",
        "marketing-agent",
        "hr-agent",
        "data-analyst",
        "devops-agent",
    ]
    loaded: dict[str, dict] = {}
    for path in _DEFINED_AGENTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            loaded[data["id"]] = data
        except Exception as e:
            logger.error("Failed to load defined agent %s: %s", path.name, e)

    # Return in defined order, then any extras
    for aid in order:
        if aid in loaded:
            agents.append(loaded[aid])
    for aid, data in loaded.items():
        if aid not in order:
            agents.append(data)

    return agents


def get(agent_id: str) -> dict[str, Any] | None:
    """Get a single defined agent template by id."""
    path = _DEFINED_AGENTS_DIR / f"{agent_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.error("Failed to load defined agent %s: %s", agent_id, e)
        return None


def to_create_request(template: dict, user_usecase: str, extra_mcps: list[str], extra_plugins: list[str]) -> dict:
    """
    Convert a defined agent template + user overrides into a CreateAgentRequest dict.

    Merges:
    - Template defaults (mcps, plugins, model, temperature)
    - User additions (extra mcps, extra plugins)
    - User's own usecase prompt (overrides or extends the default)
    """
    # Merge MCP lists — template defaults + user additions, deduplicated
    all_mcps = list(dict.fromkeys(
        template.get("default_mcps", []) + extra_mcps
    ))
    # Merge plugin lists
    all_plugins = list(dict.fromkeys(
        template.get("default_plugins", []) + extra_plugins
    ))

    return {
        "name": template["name"],
        "usecase_context": user_usecase or template["default_usecase"],
        "selected_mcp_ids": all_mcps,
        "selected_plugin_ids": all_plugins,
        "model_name": template.get("model_preference", "mistral-large-latest"),
        "temperature": template.get("temperature", 0.3),
        "max_iterations": 10,
    }