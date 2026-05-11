"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Plus,
  Rocket,
  Upload,
  WandSparkles,
} from "lucide-react";
import { Shell } from "./components/Shell";
import { MultiChoice } from "./components/MultiChoice";
import { api, type DefinedAgent, type MCPServer, type Plugin, type Registry } from "./lib/api";
import { categoryIcon, definedAgentIcon } from "./lib/icons";

const MODELS = [
  ["mistral-large-latest", "Best quality general agent"],
  ["mistral-small-latest", "Fast and cost-conscious"],
  ["codestral-latest", "Best for software work"],
  ["open-mistral-nemo", "Lightweight everyday model"],
];

export default function HomePage() {
  const router = useRouter();
  const [registry, setRegistry] = useState<Registry>({ base: [], selectable: [], builtin_skills: [] });
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [templates, setTemplates] = useState<DefinedAgent[]>([]);
  const [status, setStatus] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [customOpen, setCustomOpen] = useState(false);
  const [template, setTemplate] = useState<DefinedAgent | null>(null);
  const [toast, setToast] = useState("");

  async function load() {
    setLoading(true);
    try {
      const [reg, plug, defs, mcp] = await Promise.all([
        api.registry(),
        api.plugins(),
        api.definedAgents(),
        api.mcpStatus(),
      ]);
      setRegistry(reg);
      setPlugins(plug.plugins);
      setTemplates(defs.defined_agents);
      setStatus(mcp.active_servers || []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const goToChat = (agentId?: string) => {
    router.push(agentId ? `/chat?agent=${agentId}` : "/chat");
  };

  return (
    <Shell>
      <div className="topbar">
        <div>
          <p className="eyebrow">Agent builder</p>
          <h2 className="title">Create from a template or compose your own agent.</h2>
          <p className="subtitle">
            Agents stay linked to the parent runtime until you deploy them as standalone folders.
          </p>
        </div>
        <button className="button primary" onClick={() => setCustomOpen(true)}>
          <Plus size={17} /> Custom agent
        </button>
      </div>

      {toast && <p className="notice" style={{ marginTop: 18 }}>{toast}</p>}
      {loading && <p className="notice" style={{ marginTop: 18 }}>Loading framework inventory...</p>}

      <div className="grid">
        {templates.map((agent) => {
          const Icon = definedAgentIcon(agent.id);
          return (
            <button className="card agent-card" key={agent.id} onClick={() => setTemplate(agent)}>
              <div className="row-between">
                <div className="icon-tile"><Icon size={20} /></div>
                <ChevronRight size={18} color="var(--muted)" />
              </div>
              <div>
                <h3>{agent.name}</h3>
                <p className="muted small" style={{ marginTop: 6 }}>{agent.tagline}</p>
              </div>
              <div className="cap-list">
                {agent.capabilities.slice(0, 3).map((capability) => (
                  <span className="cap-item" key={capability}>
                    <Check size={14} /> {capability}
                  </span>
                ))}
              </div>
              <div className="pills">
                {agent.suggested_mcps.slice(0, 4).map((mcp) => (
                  <span className="pill" key={mcp}>{mcp}</span>
                ))}
              </div>
            </button>
          );
        })}
      </div>

      {customOpen && (
        <CustomBuilder
          registry={registry}
          plugins={plugins}
          status={status}
          onClose={() => setCustomOpen(false)}
          onCreated={(agentId, folder) => {
            setToast(`Created ${agentId}${folder ? ` in ${folder}` : ""}`);
            setCustomOpen(false);
            goToChat(agentId);
          }}
          onReload={load}
        />
      )}

      {template && (
        <TemplateModal
          template={template}
          registry={registry}
          plugins={plugins}
          onClose={() => setTemplate(null)}
          onCreated={(agentId, folder) => {
            setToast(`Created ${agentId}${folder ? ` in ${folder}` : ""}`);
            setTemplate(null);
            goToChat(agentId);
          }}
        />
      )}
    </Shell>
  );
}

function CustomBuilder({
  registry,
  plugins,
  status,
  onClose,
  onCreated,
  onReload,
}: {
  registry: Registry;
  plugins: Plugin[];
  status: MCPServer[];
  onClose: () => void;
  onCreated: (agentId: string, folder?: string) => void;
  onReload: () => void;
}) {
  const [name, setName] = useState("");
  const [usecase, setUsecase] = useState("");
  const [model, setModel] = useState(MODELS[0][0]);
  const [mcps, setMcps] = useState<string[]>([]);
  const [pluginIds, setPluginIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const statusMap = useMemo(() => new Map(status.map((server) => [server.id, server])), [status]);
  const mcpOptions = registry.selectable.map((server) => ({
    id: server.id,
    name: server.name,
    category: server.category,
    description: server.description,
    meta: `${server.category} - ${server.description}`,
  }));
  const pluginOptions = plugins.map((plugin) => ({
    id: plugin.id,
    name: plugin.name,
    category: plugin.category || "custom",
    description: plugin.description,
    meta: `${plugin.type} - ${plugin.source}`,
  }));

  async function createAgent() {
    if (!name.trim() || !usecase.trim()) {
      setError("Name and use-case are required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.createAgent({
        name: name.trim(),
        usecase_context: usecase.trim(),
        selected_mcp_ids: mcps,
        selected_plugin_ids: pluginIds,
        model_name: model,
      });
      if (!result.success || !result.agent_id) throw new Error(result.error || "Create failed");
      onCreated(result.agent_id, result.agent_folder);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      await api.uploadPlugin(file);
      await onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="section-head">
          <div>
            <p className="eyebrow">Custom build</p>
            <h2 className="title">Compose an agent</h2>
          </div>
          <button className="button" onClick={onClose}><ArrowLeft size={16} /> Back</button>
        </div>
        {error && <p className="notice" style={{ marginTop: 14 }}>{error}</p>}
        <div className="form-grid">
          <div className="panel">
            <div className="field">
              <label>Agent name</label>
              <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="My Research Assistant" />
            </div>
            <div className="field">
              <label>Use-case context</label>
              <textarea className="textarea" value={usecase} onChange={(event) => setUsecase(event.target.value)} placeholder="Describe what this agent does, how it should behave, and the outputs you expect." />
            </div>
            <div className="field">
              <label>Model</label>
              <select className="select" value={model} onChange={(event) => setModel(event.target.value)}>
                {MODELS.map(([id, description]) => <option value={id} key={id}>{id} - {description}</option>)}
              </select>
            </div>
            <MultiChoice title="MCP servers" options={mcpOptions} selected={mcps} onChange={setMcps} placeholder="Search servers" />
            <MultiChoice title="Plugins" options={pluginOptions} selected={pluginIds} onChange={setPluginIds} placeholder="Search plugins" />
            <div className="field">
              <label>Upload plugin JSON</label>
              <label className="button secondary" style={{ justifyContent: "flex-start" }}>
                <Upload size={16} /> Choose .plugin.json
                <input type="file" accept="application/json" hidden onChange={(event) => upload(event.target.files?.[0] || null)} />
              </label>
            </div>
          </div>
          <div className="panel stack">
            <h3>Base runtime</h3>
            {registry.base.map((server) => {
              const live = statusMap.get(server.id);
              const Icon = categoryIcon(server.category);
              const state = live?.running && live?.initialized ? "good" : live?.running ? "warn" : "bad";
              return (
                <div className="server-row" key={server.id}>
                  <span className={`dot ${state}`} />
                  <span><strong>{server.name}</strong><br /><span className="muted small">{server.category}</span></span>
                  <Icon size={16} color="var(--muted)" />
                </div>
              );
            })}
            <h3>Built-in skills</h3>
            {registry.builtin_skills.map((skill) => (
              <span className="cap-item" key={skill.id}><WandSparkles size={14} /> {skill.name}</span>
            ))}
            <button className="button primary" disabled={busy} onClick={createAgent}>
              <Rocket size={16} /> Create agent
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function TemplateModal({
  template,
  registry,
  plugins,
  onClose,
  onCreated,
}: {
  template: DefinedAgent;
  registry: Registry;
  plugins: Plugin[];
  onClose: () => void;
  onCreated: (agentId: string, folder?: string) => void;
}) {
  const [usecase, setUsecase] = useState(template.default_usecase);
  const [mcps, setMcps] = useState<string[]>(template.suggested_mcps || template.default_mcps || []);
  const [pluginIds, setPluginIds] = useState<string[]>(template.default_plugins || []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const Icon = definedAgentIcon(template.id);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const result = await api.instantiateDefinedAgent(template.id, usecase, mcps, pluginIds);
      if (!result.success || !result.agent_id) throw new Error(result.error || "Create failed");
      onCreated(result.agent_id, result.agent_folder);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  const mcpOptions = registry.selectable.map((server) => ({
    id: server.id,
    name: server.name,
    category: server.category,
    description: server.description,
    meta: `${server.category} - ${server.description}`,
  }));
  const pluginOptions = plugins.map((plugin) => ({
    id: plugin.id,
    name: plugin.name,
    category: plugin.category || "custom",
    description: plugin.description,
    meta: `${plugin.type} - ${plugin.source}`,
  }));

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="section-head">
          <div className="row-between" style={{ justifyContent: "flex-start" }}>
            <div className="icon-tile"><Icon size={20} /></div>
            <div>
              <p className="eyebrow">Template</p>
              <h2 className="title">Configure {template.name}</h2>
            </div>
          </div>
          <button className="button" onClick={onClose}><ArrowLeft size={16} /> Back</button>
        </div>
        {error && <p className="notice" style={{ marginTop: 14 }}>{error}</p>}
        <div className="form-grid">
          <div className="panel">
            <div className="field">
              <label>Use-case prompt</label>
              <textarea className="textarea" value={usecase} onChange={(event) => setUsecase(event.target.value)} />
            </div>
            <MultiChoice title="MCP servers" options={mcpOptions} selected={mcps} onChange={setMcps} placeholder="Search servers" />
            <MultiChoice title="Plugins" options={pluginOptions} selected={pluginIds} onChange={setPluginIds} placeholder="Search plugins" />
          </div>
          <div className="panel stack">
            <h3>Capabilities</h3>
            {template.capabilities.map((capability) => (
              <span className="cap-item" key={capability}><Check size={14} /> {capability}</span>
            ))}
            <h3>Example prompts</h3>
            {template.example_prompts.slice(0, 3).map((prompt) => (
              <p className="notice" key={prompt}>{prompt}</p>
            ))}
            <button className="button primary" disabled={busy} onClick={create}>
              <Rocket size={16} /> Launch {template.name}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
