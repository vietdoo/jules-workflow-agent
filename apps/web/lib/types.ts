export type Agent = {
  agent_id: string;
  display_name: string;
  description: string;
  icon: string;
  capabilities: string[];
};

export type Source = {
  name: string;
  label: string;
  default_branch?: string | null;
  branches: string[];
  metadata: Record<string, unknown>;
};

export type Session = {
  name: string;
  identifier: string;
  title?: string | null;
  state?: string | null;
  state_label: string;
  url?: string | null;
  source_name?: string | null;
  starting_branch?: string | null;
  require_plan_approval?: boolean | null;
  metadata: Record<string, unknown>;
};

export type EventRecord = {
  id: string;
  timestamp: string;
  type: string;
  conversation_id: string;
  agent_id?: string | null;
  session_name?: string | null;
  summary: string;
  data: Record<string, unknown>;
};

export type WorkspaceState = {
  conversation_id: string;
  active_agent_id: string;
  session_name?: string | null;
  selected_source?: Source | null;
  selected_branch?: string | null;
};

export type Dashboard = {
  state: WorkspaceState;
  agents: Agent[];
  event_summary: { events: number; messages: number; failures: number; sessions: number };
  recent_events: EventRecord[];
};

export type ChatMessage = {
  id: string;
  role: "operator" | "agent" | "system";
  text: string;
  timestamp: string;
  isPending?: boolean;
};
