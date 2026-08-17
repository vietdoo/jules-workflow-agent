import type { Agent, Dashboard, EventRecord, Session, Source, WorkspaceState } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8090";

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Request failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const controlApi = {
  dashboard: (conversationId: string) =>
    request<Dashboard>(`/api/dashboard?conversation_id=${encodeURIComponent(conversationId)}`),
  agents: (conversationId: string) =>
    request<{ active_agent_id: string; agents: Agent[] }>(`/api/agents?conversation_id=${encodeURIComponent(conversationId)}`),
  selectAgent: (conversationId: string, agentId: string) =>
    request<{ agent: Agent; state: WorkspaceState }>("/api/agents/active", {
      method: "PUT",
      body: { conversation_id: conversationId, agent_id: agentId },
    }),
  sources: (conversationId: string) =>
    request<Source[]>(`/api/sources?conversation_id=${encodeURIComponent(conversationId)}`),
  selectSource: (conversationId: string, sourceName: string, branch?: string) =>
    request<WorkspaceState>("/api/sources/active", {
      method: "PUT",
      body: { conversation_id: conversationId, source_name: sourceName, branch },
    }),
  sessions: (conversationId: string) =>
    request<Session[]>(`/api/sessions?conversation_id=${encodeURIComponent(conversationId)}`),
  activities: (conversationId: string, sessionName: string) =>
    request<Record<string, unknown>[]>(
      `/api/sessions/${encodeURIComponent(sessionName)}/activities?conversation_id=${encodeURIComponent(conversationId)}`,
    ),
  events: () => request<EventRecord[]>("/api/events?limit=100"),
  resetSession: (conversationId: string) =>
    request<WorkspaceState>("/api/sessions/reset", { method: "POST", body: { conversation_id: conversationId } }),
  sendMessage: (conversationId: string, prompt: string) =>
    request<{ reply: { text: string; agent_id: string }; session?: Session | null; state: WorkspaceState }>(
      "/api/messages",
      { method: "POST", body: { conversation_id: conversationId, prompt } },
    ),
  approvePlan: (conversationId: string, sessionName: string) =>
    request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sessionName)}/approve`, {
      method: "POST",
      body: { conversation_id: conversationId, confirm: true },
    }),
};

export function eventsWebSocketUrl(): string {
  return `${API_BASE.replace(/^http/, "ws")}/api/events/stream`;
}
