"use client";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8002/api/v1";

export type MCPServer = {
  id: string;
  name: string;
  category: string;
  description: string;
  required_env?: string[];
  running?: boolean;
  initialized?: boolean;
  tools?: string[];
};

export type BuiltinSkill = {
  id: string;
  name: string;
  enabled: boolean;
  description: string;
};

export type Registry = {
  base: MCPServer[];
  selectable: MCPServer[];
  builtin_skills: BuiltinSkill[];
};

export type Plugin = {
  id: string;
  name: string;
  version: string;
  type: string;
  category: string;
  description: string;
  author: string;
  tags: string[];
  source: string;
  required_env: string[];
};

export type DefinedAgent = {
  id: string;
  name: string;
  tagline: string;
  description: string;
  default_usecase: string;
  default_mcps: string[];
  default_plugins: string[];
  suggested_mcps: string[];
  suggested_plugins?: string[];
  model_preference: string;
  temperature: number;
  capabilities: string[];
  example_prompts: string[];
};

export type AgentSummary = {
  agent_id: string;
  name: string;
  model: string;
  usecase: string;
  mcps: string[];
  plugins: string[];
  deployed?: boolean;
  folder: string;
};

export type AgentDetail = {
  agent_id: string;
  name: string;
  usecase_context: string;
  selected_mcp_ids: string[];
  selected_plugin_ids: string[];
  model_name: string;
  deployed?: boolean;
};

export type ToolCall = {
  server_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  error?: string | null;
  duration_ms?: number | null;
};

export type AgentRunResponse = {
  session_id: string;
  agent_id: string;
  status: string;
  message: string;
  tool_calls: ToolCall[];
  error?: string | null;
  iterations_used: number;
  duration_ms?: number | null;
};

export type AgentMemoryMessage = {
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  metadata?: {
    tool_calls?: ToolCall[];
    iterations_used?: number;
    duration_ms?: number | null;
    status?: string;
  };
};

export type AgentMemorySession = {
  session_id: string;
  messages: AgentMemoryMessage[];
};

export type AgentMemory = {
  agent_id: string;
  agent_name?: string | null;
  schema_version: number;
  updated_at?: string | null;
  sessions: Record<string, AgentMemorySession>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData
      ? init.headers
      : { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  registry: () => request<Registry>("/mcp/registry"),
  mcpStatus: () => request<{ active_servers: MCPServer[] }>("/mcp/status"),
  plugins: () => request<{ plugins: Plugin[] }>("/plugins"),
  definedAgents: () => request<{ defined_agents: DefinedAgent[] }>("/defined-agents"),
  agents: () => request<{ agents: AgentSummary[] }>("/agents"),
  agent: (agentId: string) => request<AgentDetail>(`/agents/${agentId}`),
  agentMemory: (agentId: string) => request<AgentMemory>(`/agents/${agentId}/memory`),
  createAgent: (body: {
    name: string;
    usecase_context: string;
    selected_mcp_ids: string[];
    selected_plugin_ids: string[];
    model_name: string;
  }) => request<{
    success: boolean;
    agent_id?: string;
    agent_folder?: string;
    error?: string;
  }>("/agents", { method: "POST", body: JSON.stringify(body) }),
  instantiateDefinedAgent: (
    agentId: string,
    userUsecase: string,
    extraMcps: string[],
    extraPlugins: string[],
  ) => {
    const params = new URLSearchParams();
    params.set("user_usecase", userUsecase);
    extraMcps.forEach((mcp) => params.append("extra_mcps", mcp));
    extraPlugins.forEach((plugin) => params.append("extra_plugins", plugin));
    return request<{
      success: boolean;
      agent_id?: string;
      agent_folder?: string;
      error?: string;
    }>(`/defined-agents/${agentId}/instantiate?${params.toString()}`, {
      method: "POST",
    });
  },
  uploadPlugin: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<{ success: boolean; message: string; plugin_id: string }>(
      "/plugins/upload",
      { method: "POST", body },
    );
  },
  deployAgent: (agentId: string) => request<{
    success: boolean;
    agent_id?: string;
    agent_folder?: string;
    error?: string;
  }>(`/agents/${agentId}/deploy`, { method: "POST", body: JSON.stringify({}) }),
  clearSession: (agentId: string, sessionId: string) =>
    request<{ success: boolean }>(
      `/agents/${agentId}/memory/${sessionId}`,
      { method: "DELETE" },
    ),
  runAgent: (body: { session_id: string; agent_id: string; message: string }) =>
    request<{ success: boolean; response?: AgentRunResponse; error?: string }>(
      "/agent/run",
      { method: "POST", body: JSON.stringify(body) },
    ),
};
