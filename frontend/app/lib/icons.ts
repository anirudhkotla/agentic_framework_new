import {
  BarChart3,
  BookOpenText,
  Bot,
  BriefcaseBusiness,
  Code2,
  Database,
  FileText,
  Globe2,
  HeartHandshake,
  Megaphone,
  Network,
  ServerCog,
  Settings2,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  type LucideIcon,
} from "lucide-react";

export function definedAgentIcon(id: string): LucideIcon {
  const map: Record<string, LucideIcon> = {
    "coding-agent": Code2,
    "content-writer": BookOpenText,
    "marketing-agent": Megaphone,
    "hr-agent": HeartHandshake,
    "data-analyst": BarChart3,
    "devops-agent": ServerCog,
  };
  return map[id] || Bot;
}

export function categoryIcon(category: string): LucideIcon {
  const map: Record<string, LucideIcon> = {
    web: Globe2,
    dev: TerminalSquare,
    database: Database,
    productivity: FileText,
    business: BriefcaseBusiness,
    devops: ServerCog,
    ai_ml: Sparkles,
    custom: Settings2,
    storage: Network,
    memory: ShieldCheck,
  };
  return map[category] || Settings2;
}
