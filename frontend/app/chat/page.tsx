"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Bot,
  Boxes,
  CheckCircle2,
  Eraser,
  ExternalLink,
  FolderArchive,
  MessageSquareText,
  Rocket,
  Send,
  Wrench,
} from "lucide-react";
import { Shell } from "../components/Shell";
import {
  api,
  type AgentDetail,
  type AgentMemory,
  type AgentMemoryMessage,
  type AgentRunResponse,
  type AgentSummary,
  type MCPServer,
  type ToolCall,
} from "../lib/api";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  meta?: string;
  tool_calls?: ToolCall[];
};

export default function ChatPage() {
  return (
    <Suspense fallback={<Shell><div className="notice">Loading chat...</div></Shell>}>
      <ChatContent />
    </Suspense>
  );
}

function ChatContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedId, setSelectedId] = useState(searchParams.get("agent") || "");
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState(makeSessionId());
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingMemory, setLoadingMemory] = useState(false);
  const [error, setError] = useState("");

  const loadAgents = useCallback(async () => {
    const result = await api.agents();
    setAgents(result.agents);
    if (!selectedId && result.agents[0]) {
      setSelectedId(result.agents[0].agent_id);
    }
  }, [selectedId]);

  async function loadStatus() {
    const result = await api.mcpStatus();
    setServers(result.active_servers || []);
  }

  useEffect(() => {
    loadAgents().catch(() => setError("Could not load agents. Is the API running?"));
    loadStatus().catch(() => undefined);
  }, [loadAgents]);

  useEffect(() => {
    if (!selectedId) return;
    api.agent(selectedId).then(setDetail).catch(() => setDetail(null));
    setLoadingMemory(true);
    api.agentMemory(selectedId)
      .then((memory) => {
        const restored = memoryToChatMessages(memory);
        setMessages(restored.messages);
        setSessionId(restored.sessionId || makeSessionId());
      })
      .catch(() => {
        setMessages([]);
        setSessionId(makeSessionId());
      })
      .finally(() => setLoadingMemory(false));
    router.replace(`/chat?agent=${selectedId}`);
  }, [selectedId, router]);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.agent_id === selectedId),
    [agents, selectedId],
  );

  async function clearMemory() {
    if (selectedId) {
      await api.clearSession(selectedId, sessionId).catch(() => undefined);
    }
    setSessionId(makeSessionId());
    setMessages([]);
  }

  async function deploy() {
    if (!selectedId) return;
    setBusy(true);
    setError("");
    try {
      await api.deployAgent(selectedId);
      const updated = await api.agent(selectedId);
      setDetail(updated);
      await loadAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deploy failed");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const message = input.trim();
    if (!message || !selectedId) return;
    setMessages((current) => [...current, { role: "user", content: message }]);
    setInput("");
    setBusy(true);
    setError("");
    try {
      const result = await api.runAgent({
        session_id: sessionId,
        agent_id: selectedId,
        message,
      });
      if (!result.success || !result.response) {
        throw new Error(result.error || "Agent run failed");
      }
      setMessages((current) => [...current, toAssistantMessage(result.response!)]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Agent run failed";
      setError(msg);
      setMessages((current) => [...current, { role: "assistant", content: `Error: ${msg}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="topbar">
        <div>
          <p className="eyebrow">Conversation console</p>
          <h2 className="title">Chat with active agents.</h2>
          <p className="subtitle">
            Select a linked or deployed agent, inspect tool calls, and keep sessions isolated.
          </p>
        </div>
        <button className="button" onClick={() => router.push("/")}>
          <Bot size={16} /> Build agent
        </button>
      </div>

      {error && <p className="notice" style={{ marginTop: 18 }}>{error}</p>}

      {agents.length === 0 ? (
        <div className="panel" style={{ marginTop: 22 }}>
          <MessageSquareText size={24} color="var(--accent)" />
          <h3 style={{ marginTop: 12 }}>No agents yet</h3>
          <p className="muted">Create an agent on the Home page, then return here to chat.</p>
          <button className="button primary" onClick={() => router.push("/")}>
            <Rocket size={16} /> Create agent
          </button>
        </div>
      ) : (
        <>
          <div className="panel" style={{ marginTop: 22 }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Select agent</label>
              <select
                className="select"
                value={selectedId}
                onChange={(event) => {
                  setSelectedId(event.target.value);
                  setMessages([]);
                  setSessionId(makeSessionId());
                }}
              >
                {agents.map((agent) => (
                  <option value={agent.agent_id} key={agent.agent_id}>
                    {agent.name} - {agent.agent_id} - {agent.model}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="chat-layout">
            <div>
              <div className="messages">
                {messages.length === 0 && (
                  <div className="notice">
                    {loadingMemory
                      ? "Loading saved memory..."
                      : `Start a session with ${selectedAgent?.name || selectedId}. The response metadata and tool calls will appear inline after each assistant message.`}
                  </div>
                )}
                {messages.map((message, index) => (
                  <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
                    {message.content}
                    {message.meta && <div className="tool-call">{message.meta}</div>}
                    {message.tool_calls?.map((tool, toolIndex) => (
                      <ToolCallView tool={tool} key={`${tool.tool_name}-${toolIndex}`} />
                    ))}
                  </div>
                ))}
              </div>
              <div className="composer">
                <textarea
                  className="textarea"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                      event.preventDefault();
                      send();
                    }
                  }}
                  placeholder={`Message ${selectedAgent?.name || "agent"}`}
                  style={{ minHeight: 58 }}
                />
                <button className="button primary" disabled={busy || !input.trim()} onClick={send}>
                  <Send size={16} /> Send
                </button>
              </div>
            </div>

            <aside className="stack">
              <div className="panel stack">
                <h3>Session</h3>
                {detail && (
                  <>
                    <p className="muted small" style={{ marginBottom: 0 }}>{detail.model_name}</p>
                    <div className="pills">
                      {detail.selected_mcp_ids.map((mcp) => <span className="pill" key={mcp}>{mcp}</span>)}
                      {detail.selected_plugin_ids.map((plugin) => <span className="pill" key={plugin}>{plugin}</span>)}
                    </div>
                    <p className="status-line">
                      {detail.deployed ? <CheckCircle2 size={16} /> : <FolderArchive size={16} />}
                      {detail.deployed ? "Standalone deployed" : "Linked to parent runtime"}
                    </p>
                  </>
                )}
                <button className="button" onClick={clearMemory}><Eraser size={16} /> Clear memory</button>
                {detail && !detail.deployed && (
                  <button className="button primary" disabled={busy} onClick={deploy}>
                    <FolderArchive size={16} /> Deploy standalone
                  </button>
                )}
                {selectedId && (
                  <a className="button secondary" href={`/chat?agent=${selectedId}`}>
                    <ExternalLink size={16} /> Direct chat URL
                  </a>
                )}
              </div>

              <div className="panel">
                <div className="row-between">
                  <h3 style={{ marginBottom: 0 }}>MCP status</h3>
                  <Boxes size={17} color="var(--muted)" />
                </div>
                <div style={{ marginTop: 10 }}>
                  {servers.map((server) => {
                    const state = server.running && server.initialized ? "good" : server.running ? "warn" : "bad";
                    return (
                      <div className="server-row" key={server.id}>
                        <span className={`dot ${state}`} />
                        <span>
                          <strong>{server.name}</strong>
                          <br />
                          <span className="muted small">
                            {(server.tools || []).length} tools
                          </span>
                        </span>
                        <Wrench size={15} color="var(--muted)" />
                      </div>
                    );
                  })}
                  {servers.length === 0 && <p className="notice">No MCP servers reported.</p>}
                </div>
              </div>
            </aside>
          </div>
        </>
      )}
    </Shell>
  );
}

function ToolCallView({ tool }: { tool: ToolCall }) {
  return (
    <details className="tool-call">
      <summary>{tool.server_id} - {tool.tool_name}</summary>
      <pre style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
        {JSON.stringify({ arguments: tool.arguments, result: tool.result, error: tool.error }, null, 2)}
      </pre>
    </details>
  );
}

function toAssistantMessage(response: AgentRunResponse): ChatMessage {
  return {
    role: "assistant",
    content: response.message,
    tool_calls: response.tool_calls || [],
    meta: `${response.iterations_used} iterations - ${response.duration_ms || 0} ms - ${response.status}`,
  };
}

function memoryToChatMessages(memory: AgentMemory): {
  messages: ChatMessage[];
  sessionId: string | null;
} {
  const sessions = Object.values(memory.sessions || {});
  const latestSession = sessions
    .slice()
    .sort((a, b) => latestMessageTime(b) - latestMessageTime(a))[0];

  const messages = sessions
    .flatMap((session) => session.messages || [])
    .sort((a, b) => Date.parse(a.timestamp || "") - Date.parse(b.timestamp || ""))
    .map(memoryMessageToChatMessage)
    .filter((message): message is ChatMessage => Boolean(message));

  return {
    messages,
    sessionId: latestSession?.session_id || null,
  };
}

function memoryMessageToChatMessage(message: AgentMemoryMessage): ChatMessage | null {
  if (message.role !== "user" && message.role !== "assistant") {
    return null;
  }

  const metadata = message.metadata || {};
  const meta = metadata.status
    ? `${metadata.iterations_used || 0} iterations - ${metadata.duration_ms || 0} ms - ${metadata.status}`
    : undefined;

  return {
    role: message.role,
    content: message.content,
    tool_calls: metadata.tool_calls || [],
    meta,
  };
}

function latestMessageTime(session: { messages?: AgentMemoryMessage[] }) {
  return Math.max(
    0,
    ...(session.messages || []).map((message) => Date.parse(message.timestamp || "") || 0),
  );
}

function makeSessionId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `session-${Date.now()}`;
}
