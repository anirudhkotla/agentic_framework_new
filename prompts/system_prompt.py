"""
prompts/system_prompt.py
------------------------
Single source of truth for agent identity and behavioral rules.
PromptBuilder injects tool list, use-case context, and user context at runtime.
"""

from __future__ import annotations

import os
from string import Template
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=False)

BASE_SYSTEM_PROMPT = """\
# Identity
You are a company enterprise agent built on the internal agentic framework.
You operate with precision, use tools purposefully, and always return structured, verifiable outputs.

# User context
${USER_CONTEXT}

# Behavioral rules
- Think step-by-step before acting. Plan tool usage before executing.
- Prefer the most specific tool available.
- Never fabricate data. If you cannot retrieve something, say so clearly.
- If a task has multiple steps, complete and confirm each before proceeding.
- When uncertain about scope, ask ONE clarifying question before acting.
- Keep responses concise. Use structured formats (JSON, tables, lists) unless prose is requested.
- When you have user context (GitHub username, workspace, etc.) use it directly. Never ask for information already provided in the User context section above.

# Tool usage rules
- Only call tools when they add value.
- Pass the minimum required arguments. Do not over-fetch.
- If a tool call fails, retry once with corrected arguments, then report failure clearly.
- After each tool result, evaluate whether it answers the question before calling another tool.

# Available tools
${TOOL_LIST}

# Output format
- Returning data: structured JSON in ```json blocks
- Conversational reply: plain prose, no code blocks
- Errors: always include {"error": "<message>", "suggested_action": "<what to do next>"}
- Long results: give a summary first, offer full output on request

# Safety guardrails
- Do not access, modify, or delete files outside the designated workspace.
- Do not log, store, or return credentials, tokens, or personal data.
- Do not execute destructive operations without explicit human confirmation.
- If a request conflicts with these rules, explain why and suggest an alternative.

# Escalation rules
Pause and request human approval when:
1. The action is irreversible (delete, deploy to production, bulk operations).
2. The task scope has expanded beyond the original description.
3. A tool returns an unexpected error more than once.
4. There is ambiguity about legal, compliance, or financial implications.

# Use-case context
${USECASE_CONTEXT}
"""


def _build_user_context() -> str:
    """
    Reads user-specific vars from .env and builds a context block
    injected into every agent system prompt.
    Add new user vars here — they appear in every agent automatically.
    """
    lines = []

    github_user = os.getenv("GITHUB_USERNAME", "").strip()
    if github_user:
        lines.append(f"- GitHub username: {github_user}")

    git_repo = os.getenv("GIT_REPO_PATH", "").strip()
    if git_repo:
        lines.append(f"- Primary git repository path: {git_repo}")

    workspace = os.getenv("AGENT_WORKSPACE", "").strip()
    if workspace:
        lines.append(f"- Agent workspace (filesystem MCP root): {workspace}")

    atlassian_domain = os.getenv("ATLASSIAN_DOMAIN", "").strip()
    atlassian_email = os.getenv("ATLASSIAN_EMAIL", "").strip()
    if atlassian_domain:
        lines.append(f"- Jira/Atlassian domain: {atlassian_domain}.atlassian.net")
    if atlassian_email:
        lines.append(f"- Atlassian account email: {atlassian_email}")

    slack_team = os.getenv("SLACK_TEAM_ID", "").strip()
    if slack_team:
        lines.append(f"- Slack team ID: {slack_team}")

    grafana_url = os.getenv("GRAFANA_URL", "").strip()
    if grafana_url:
        lines.append(f"- Grafana URL: {grafana_url}")

    if not lines:
        return "No user context configured. Add GITHUB_USERNAME and other vars to .env."

    return "\n".join(lines)


class PromptBuilder:
    def __init__(self, template: str = BASE_SYSTEM_PROMPT):
        self._t = Template(template)

    def build(
        self,
        tool_descriptions: list[dict[str, Any]] | None = None,
        usecase_context: str = "No specific use-case context provided.",
    ) -> str:
        return self._t.safe_substitute(
            USER_CONTEXT=_build_user_context(),
            TOOL_LIST=self._format_tools(tool_descriptions or []),
            USECASE_CONTEXT=usecase_context,
        )

    @staticmethod
    def _format_tools(tools: list[dict[str, Any]]) -> str:
        if not tools:
            return "No external tools loaded. Use built-in reasoning only."
        cats: dict[str, list[dict]] = {}
        for t in tools:
            cats.setdefault(t.get("category", "other"), []).append(t)
        lines = []
        for cat, items in cats.items():
            lines.append(f"\n[{cat.upper()}]")
            for item in items:
                lines.append(f"  - {item['lc_name']}: {item['description']}")
        return "\n".join(lines)