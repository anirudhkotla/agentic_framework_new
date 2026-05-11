"""
core/models.py
--------------
Single source of truth for all data shapes.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv(override=False)


def _default_model() -> str:
    return os.getenv("DEFAULT_MODEL", "mistral-large-latest")


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting_for_human"
    ERROR = "error"
    COMPLETE = "complete"


class InputType(str, Enum):
    TEXT = "text"
    CODE = "code"
    IMAGE = "image"


class ChatMessage(BaseModel):
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    duration_ms: float | None = None


class AgentConfig(BaseModel):
    agent_id: str
    name: str
    usecase_context: str
    selected_mcp_ids: list[str] = Field(default_factory=list)
    selected_plugin_ids: list[str] = Field(default_factory=list)
    model_name: str = Field(default_factory=_default_model)
    max_iterations: int = Field(default=10, ge=1, le=50)
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    stream: bool = False
    memory_enabled: bool = True

    @field_validator("agent_id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("agent_id must be alphanumeric with hyphens/underscores")
        return v.lower()


class UserInput(BaseModel):
    session_id: str
    agent_id: str
    input_type: InputType = InputType.TEXT
    content: str
    image_base64: str | None = None

    @field_validator("content")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Content cannot be empty")
        return v.strip()


class AgentResponse(BaseModel):
    session_id: str
    agent_id: str
    status: AgentStatus
    message: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    error: str | None = None
    iterations_used: int = 0
    duration_ms: float | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CreateAgentRequest(BaseModel):
    name: str
    usecase_context: str
    selected_mcp_ids: list[str] = Field(default_factory=list)
    selected_plugin_ids: list[str] = Field(default_factory=list)
    model_name: str = Field(default_factory=_default_model)
    max_iterations: int = 10
    temperature: float = 0.3


class CreateAgentResponse(BaseModel):
    success: bool
    agent_id: str | None = None
    config: AgentConfig | None = None
    agent_folder: str | None = None
    error: str | None = None


class RunAgentRequest(BaseModel):
    session_id: str
    agent_id: str
    message: str
    input_type: InputType = InputType.TEXT
    image_base64: str | None = None


class RunAgentResponse(BaseModel):
    success: bool
    response: AgentResponse | None = None
    error: str | None = None


class MCPStatusResponse(BaseModel):
    active_servers: list[dict[str, Any]]
    builtin_skills: list[dict[str, Any]]
    total_active: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    version: str
    active_agents: int
    active_mcp_servers: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)