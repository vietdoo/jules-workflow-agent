/** Studio browser calls stay same-origin while agent work completes asynchronously. */
import type { Agent, Dashboard, EventRecord, Session, Source, WorkspaceState } from "./types";

/**
 * Browser requests deliberately stay on the Studio origin. Next.js proxies `/api/*`
 * to the local FastAPI control plane, so a remote browser never attempts to reach
 * its own loopback address and no public API-base value is baked into the bundle.
 */
const API_BASE = "";

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
  events: (conversationId: string) =>
    request<EventRecord[]>(`/api/events?conversation_id=${encodeURIComponent(conversationId)}&limit=100`),
  resetSession: (conversationId: string) =>
    request<WorkspaceState>("/api/sessions/reset", { method: "POST", body: { conversation_id: conversationId } }),
  sendMessage: (conversationId: string, prompt: string) =>
    request<{ accepted: boolean; submission_id: string; state: WorkspaceState }>(
      "/api/messages",
      { method: "POST", body: { conversation_id: conversationId, prompt } },
    ),
  approvePlan: (conversationId: string, sessionName: string) =>
    request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sessionName)}/approve`, {
      method: "POST",
      body: { conversation_id: conversationId, confirm: true },
    }),
  attachSession: (conversationId: string, sessionName: string) =>
    request<WorkspaceState>(`/api/sessions/${encodeURIComponent(sessionName)}/attach`, {
      method: "POST",
      body: { conversation_id: conversationId },
    }),
};

export function eventsWebSocketUrl(conversationId: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/api/events/stream?conversation_id=${encodeURIComponent(conversationId)}`;
}
