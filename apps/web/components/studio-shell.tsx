"use client";
// Design reminder: preserve the warm, calm control-room aesthetic while making long-running Jules work legible.

import {
  Activity,
  ArrowUpRight,
  Bot,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  Command,
  FileText,
  LayoutDashboard,
  Menu,
  MessageSquareText,
  PanelRight,
  Plus,
  RefreshCw,
  SendHorizontal,
  Settings2,
  Sparkles,
  TerminalSquare,
  Workflow,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { controlApi, eventsWebSocketUrl } from "../lib/api";
import type { Agent, ChatMessage, Dashboard, EventRecord, Session, Source, WorkspaceState } from "../lib/types";

const CONVERSATION_ID = "web:local";

const navItems = [
  { id: "workspace", label: "Workspace", icon: LayoutDashboard },
  { id: "sessions", label: "Sessions", icon: Workflow },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "journals", label: "Local journals", icon: FileText },
  { id: "logs", label: "Harness logs", icon: TerminalSquare },
  { id: "settings", label: "Settings", icon: Settings2 },
] as const;

type ViewId = (typeof navItems)[number]["id"];

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function compactSessionName(session?: string | null): string {
  if (!session) return "No active session";
  return session.split("/").at(-1) ?? session;
}

function stateTone(state?: string | null): string {
  const value = state?.toLowerCase() ?? "";
  if (value.includes("complete") || value.includes("success")) return "sage";
  if (value.includes("fail") || value.includes("error")) return "rose";
  if (value.includes("plan") || value.includes("wait")) return "gold";
  return "slate";
}

function eventToMessage(event: EventRecord, settledSubmissionIds: Set<string>): ChatMessage | null {
  if (event.type === "message.submitted") {
    return {
      id: event.id,
      role: "operator",
      text: String(event.data.prompt ?? event.summary),
      timestamp: event.timestamp,
      isPending: !settledSubmissionIds.has(event.id),
      submissionId: event.id,
    };
  }
  if (event.type === "message.completed") {
    return {
      id: event.id,
      role: "agent",
      text: String(event.data.reply ?? event.summary),
      timestamp: event.timestamp,
      submissionId: String(event.data.submitted_event_id ?? ""),
    };
  }
  if (event.type === "message.failed") {
    return {
      id: event.id,
      role: "system",
      text: `Jules could not complete this task: ${event.summary}`,
      timestamp: event.timestamp,
      submissionId: String(event.data.submitted_event_id ?? ""),
    };
  }
  if (event.type === "message.progress") {
    return {
      id: event.id,
      role: "agent",
      text: String(event.summary),
      timestamp: event.timestamp,
      submissionId: String(event.data.submitted_event_id ?? ""),
    };
  }
  if (event.type.startsWith("provider.")) {
    const kind = event.type.replace("provider.", "").replaceAll(".", " ");
    return {
      id: event.id,
      role: "agent",
      text: `${kind[0]?.toUpperCase() ?? "J"}${kind.slice(1)} · ${event.summary}`,
      timestamp: event.timestamp,
      submissionId: String(event.data.submitted_event_id ?? ""),
    };
  }
  if (event.type === "session.completed" || event.type === "session.failed") {
    return {
      id: event.id,
      role: event.type === "session.failed" ? "system" : "agent",
      text: event.type === "session.failed" ? `Jules session needs attention: ${event.summary}` : event.summary,
      timestamp: event.timestamp,
      submissionId: String(event.data.submitted_event_id ?? ""),
    };
  }
  return null;
}

function messagesFromEvents(events: EventRecord[]): ChatMessage[] {
  const settledSubmissionIds = new Set(
    events
      .filter((event) => ["message.completed", "message.failed", "session.completed", "session.failed"].includes(event.type))
      .map((event) => String(event.data.submitted_event_id ?? ""))
      .filter(Boolean),
  );
  return events
    .map((event) => eventToMessage(event, settledSubmissionIds))
    .filter((item): item is ChatMessage => item !== null)
    .reverse();
}

export function StudioShell() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [view, setView] = useState<ViewId>("workspace");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const messageEndRef = useRef<HTMLDivElement>(null);

  const state: WorkspaceState | null = dashboard?.state ?? null;
  const agents = dashboard?.agents ?? [];
  const activeAgent = agents.find((agent) => agent.agent_id === state?.active_agent_id) ?? agents[0];
  const selectedSource = state?.selected_source ?? null;
  const activeSession = sessions.find((session) => session.name === state?.session_name);

  const hydrate = useCallback(async (quiet = false) => {
    if (!quiet) setIsLoading(true);
    try {
      const [nextDashboard, nextSources, nextSessions, nextEvents] = await Promise.all([
        controlApi.dashboard(CONVERSATION_ID),
        controlApi.sources(CONVERSATION_ID),
        controlApi.sessions(CONVERSATION_ID),
        controlApi.events(CONVERSATION_ID),
      ]);
      setDashboard(nextDashboard);
      setSources(nextSources);
      setSessions(nextSessions);
      setEvents(nextEvents);
      setMessages(messagesFromEvents(nextEvents));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The local control plane is unavailable.");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  useEffect(() => {
    const socket = new WebSocket(eventsWebSocketUrl(CONVERSATION_ID));
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as EventRecord;
      setEvents((current) => [event, ...current.filter((item) => item.id !== event.id)].slice(0, 100));
      const chatMessage = eventToMessage(event, new Set());
      if (chatMessage) {
        setMessages((current) => {
          const settlesPrompt = ["message.completed", "message.failed", "session.completed", "session.failed"].includes(event.type);
          const withSettledPrompt = chatMessage.submissionId && settlesPrompt
            ? current.map((item) => item.submissionId === chatMessage.submissionId ? { ...item, isPending: false } : item)
            : current;
          return [...withSettledPrompt.filter((item) => item.id !== chatMessage.id), chatMessage];
        });
      }
    };
    return () => socket.close();
  }, []);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  const refresh = async () => {
    setIsRefreshing(true);
    await hydrate(true);
  };

  const chooseAgent = async (agent: Agent) => {
    try {
      const result = await controlApi.selectAgent(CONVERSATION_ID, agent.agent_id);
      setDashboard((current) => (current ? { ...current, state: result.state } : current));
      await hydrate(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not activate agent.");
    }
  };

  const chooseSource = async (source: Source) => {
    try {
      const nextState = await controlApi.selectSource(CONVERSATION_ID, source.name, source.default_branch ?? undefined);
      setDashboard((current) => (current ? { ...current, state: nextState } : current));
      await hydrate(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not select source.");
    }
  };

  const chooseBranch = async (branch: string) => {
    if (!selectedSource) return;
    try {
      const nextState = await controlApi.selectSource(CONVERSATION_ID, selectedSource.name, branch);
      setDashboard((current) => (current ? { ...current, state: nextState } : current));
      await hydrate(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not select the branch.");
    }
  };

  const submitPrompt = async (event: FormEvent) => {
    event.preventDefault();
    const nextPrompt = prompt.trim();
    if (!nextPrompt || isSending) return;
    const optimistic: ChatMessage = {
      id: `pending-${Date.now()}`,
      role: "operator",
      text: nextPrompt,
      timestamp: new Date().toISOString(),
      isPending: true,
    };
    setMessages((current) => [...current, optimistic]);
    setPrompt("");
    setIsSending(true);
    try {
      const result = await controlApi.sendMessage(CONVERSATION_ID, nextPrompt);
      setMessages((current) => [
        ...current.filter((item) => item.id !== optimistic.id && item.id !== result.submission_id),
        { ...optimistic, id: result.submission_id, isPending: true, submissionId: result.submission_id },
      ]);
      setDashboard((current) => (current ? { ...current, state: result.state } : current));
      await hydrate(true);
    } catch (caught) {
      setMessages((current) => current.filter((item) => item.id !== optimistic.id));
      setError(caught instanceof Error ? caught.message : "The agent could not complete that request.");
    } finally {
      setIsSending(false);
    }
  };

  const resetSession = async () => {
    try {
      const nextState = await controlApi.resetSession(CONVERSATION_ID);
      setDashboard((current) => (current ? { ...current, state: nextState } : current));
      setMessages([]);
      await hydrate(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not reset the local session.");
    }
  };

  const approvePlan = async (sessionName = state?.session_name) => {
    if (!sessionName) return;
    try {
      await controlApi.approvePlan(CONVERSATION_ID, sessionName);
      await hydrate(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not approve the plan.");
    }
  };

  const attachSession = async (session: Session) => {
    try {
      const nextState = await controlApi.attachSession(CONVERSATION_ID, session.name);
      setDashboard((current) => (current ? { ...current, state: nextState } : current));
      setView("workspace");
      await hydrate(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not attach that Jules session.");
    }
  };

  const summary = dashboard?.event_summary ?? { events: 0, messages: 0, failures: 0, sessions: 0 };
  const sessionNeedsApproval = activeSession?.state?.toLowerCase().includes("plan") ?? false;
  const sessionRows = useMemo(() => sessions, [sessions]);
  const workPending = messages.some((message) => message.role === "operator" && message.isPending);

  return (
    <main className="studio-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}>
        <div className="brand-row">
          <div className="mark" aria-hidden="true"><span /><span /><span /></div>
          <div><strong>Jules</strong><em>Workflow Studio</em></div>
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="Close navigation"><X size={17} /></button>
        </div>

        <div className="workspace-chip"><span className="live-dot" />Local harness <span className="kbd">⌘K</span></div>

        <nav className="nav-list" aria-label="Studio navigation">
          <p className="nav-label">CONTROL ROOM</p>
          {navItems.slice(0, 3).map((item) => {
            const Icon = item.icon;
            return <button key={item.id} className={`nav-item ${view === item.id ? "nav-item--active" : ""}`} onClick={() => { setView(item.id); setSidebarOpen(false); }}><Icon size={17} />{item.label}</button>;
          })}
          <p className="nav-label nav-label--lower">WORKSPACE</p>
          {navItems.slice(3).map((item) => {
            const Icon = item.icon;
            return <button key={item.id} className={`nav-item ${view === item.id ? "nav-item--active" : ""}`} onClick={() => { setView(item.id); setSidebarOpen(false); }}><Icon size={17} />{item.label}{item.id === "journals" && <span className="nav-count">{summary.events}</span>}</button>;
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="operator-avatar">V</div>
          <div><strong>Local operator</strong><span>127.0.0.1 workspace</span></div>
          <button className="icon-button" aria-label="Open local Studio settings" onClick={() => setView("settings")}><ChevronDown size={15} /></button>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Open navigation"><Menu size={18} /></button>
          <div className="breadcrumb"><span>Control room</span><span>/</span><strong>{view === "workspace" ? "Orchestrate" : view[0].toUpperCase() + view.slice(1)}</strong></div>
          <div className="topbar-actions">
            <span className="connection"><CircleDot size={14} />API {error ? "attention" : "connected"}</span>
            <button className="quiet-button" onClick={refresh} disabled={isRefreshing}><RefreshCw size={15} className={isRefreshing ? "spin" : ""} />Refresh</button>
          </div>
        </header>

        {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}

        <div className="workspace-grid">
          <section className="main-panel">
            {view === "workspace" && <>
              <div className="hero-intro">
                <div>
                  <p className="eyebrow"><Sparkles size={14} />AGENT ORCHESTRATION</p>
                  <h1>Build with a clear<br /><i>line of sight.</i></h1>
                  <p>Route work, inspect agent state, and keep a local record of every decision.</p>
                </div>
                <div className="hero-stamp"><span>LOCAL</span><strong>01</strong><small>fully auditable</small></div>
              </div>

              <div className="metric-row">
                <Metric label="Logged events" value={summary.events} hint="append-only" />
                <Metric label="Agent exchanges" value={summary.messages} hint="in this harness" />
                <Metric label="Remote sessions" value={summary.sessions} hint="visible to agent" />
                <Metric label="Exceptions" value={summary.failures} hint="recorded locally" danger={summary.failures > 0} />
              </div>

              <section className="chat-card">
                <div className="chat-card__header">
                  <div><span className="status-orb" /><span>Live conversation</span><em>{compactSessionName(state?.session_name)}</em></div>
                  <button className="text-button" onClick={resetSession}><Plus size={14} />New thread</button>
                </div>
                <div className="messages" aria-live="polite">
                  {isLoading && <div className="empty-state"><RefreshCw className="spin" size={18} />Loading your local workspace…</div>}
                  {!isLoading && messages.length === 0 && <div className="empty-state empty-state--rich"><div className="signal-glyph"><Command size={20} /></div><strong>What should the agent work on?</strong><p>Choose a source, then describe an outcome. The complete exchange is written to your local JSON and Markdown journal.</p></div>}
                  {messages.map((message) => <ChatBubble key={message.id} message={message} agent={activeAgent} />)}
                  {(isSending || workPending) && <div className="typing"><span /><span /><span />{isSending ? "Submitting to Jules" : "Jules is working — activity will appear here"}</div>}
                  <div ref={messageEndRef} />
                </div>
                <form className="composer" onSubmit={submitPrompt}>
                  <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Describe the work, a question, or a change…" rows={2} />
                  <div className="composer__footer"><span><Command size={13} />Enter to send · Shift + Enter for newline</span><button type="submit" disabled={!prompt.trim() || isSending || workPending} className="send-button">Send <SendHorizontal size={16} /></button></div>
                </form>
              </section>
            </>}

            {view === "sessions" && <section className="data-view"><div className="view-heading"><div><p className="eyebrow">REMOTE WORK</p><h2>Session ledger</h2></div><button className="quiet-button" onClick={refresh}><RefreshCw size={15} />Sync</button></div>{sessionRows.length === 0 ? <EmptyCard title="No sessions visible" text="Start a conversation to create a Jules session, or refresh to retrieve remote work." /> : <div className="session-list">{sessionRows.map((session) => <SessionCard key={session.name} session={session} onApprove={() => approvePlan(session.name)} onAttach={() => attachSession(session)} active={session.name === state?.session_name} />)}</div>}</section>}

            {view === "activity" && <section className="data-view"><div className="view-heading"><div><p className="eyebrow">LOCAL AUDIT TRAIL</p><h2>Workflow activity</h2></div><span className="chip">JSONL + Markdown</span></div><div className="timeline">{events.length === 0 ? <EmptyCard title="No local events yet" text="Your completed actions will appear here and in runtime/events and runtime/journals." /> : events.map((event) => <TimelineEvent key={event.id} event={event} />)}</div></section>}
            {view === "journals" && <section className="data-view"><div className="view-heading"><div><p className="eyebrow">DURABLE WORKSPACE RECORD</p><h2>Local journals</h2></div><span className="chip">conversation scoped</span></div><p className="view-intro">Every displayed prompt, provider activity, and terminal outcome is recorded locally as JSONL and Markdown. This view intentionally shows only the current Studio conversation.</p><div className="timeline">{events.length === 0 ? <EmptyCard title="No journal entries yet" text="Send a task to create durable local records for this workspace." /> : events.map((event) => <TimelineEvent key={event.id} event={event} />)}</div></section>}
            {view === "logs" && <section className="data-view"><div className="view-heading"><div><p className="eyebrow">SAFE RUNTIME DIAGNOSTICS</p><h2>Harness logs</h2></div><span className="chip">operator safe</span></div><div className="diagnostic-grid"><DiagnosticCard label="Control plane" value={error ? "Attention needed" : "Connected"} detail="FastAPI routes and real-time event stream" tone={error ? "rose" : "sage"} /><DiagnosticCard label="Active session" value={compactSessionName(state?.session_name)} detail={activeSession?.state_label ?? "No remote session attached"} /><DiagnosticCard label="Event journal" value={`${summary.events} records`} detail="Scoped to this Studio conversation" /><DiagnosticCard label="Raw process logs" value="Host-only" detail="Sensitive server logs are intentionally not exposed through the public Studio." /></div></section>}
            {view === "settings" && <section className="data-view"><div className="view-heading"><div><p className="eyebrow">LOCAL HARNESS CONTEXT</p><h2>Settings</h2></div><span className="chip">read-only remotely</span></div><div className="settings-card"><strong>Current routing</strong><dl><dt>Agent</dt><dd>{activeAgent?.display_name ?? "Loading"}</dd><dt>Repository</dt><dd>{selectedSource?.label ?? "No source selected"}</dd><dt>Branch</dt><dd>{state?.selected_branch ?? selectedSource?.default_branch ?? "Not selected"}</dd><dt>Connected sources</dt><dd>{sources.length}</dd></dl><p>Credentials, API endpoint, and webhook settings remain local `.env` configuration and are deliberately not editable from this exposed Studio. Select a repository, branch, agent, or remote session through the working controls in this interface.</p></div></section>}
          </section>

          <aside className="orchestration-rail">
            <section className="rail-section rail-section--agent">
              <div className="rail-heading"><span>ACTIVE AGENT</span><button onClick={() => setAgentPickerOpen((current) => !current)} aria-label="Show available agents" aria-expanded={agentPickerOpen}><PanelRight size={16} /></button></div>
              <div className="agent-card"><div className="agent-monogram">{activeAgent?.icon || "J"}</div><div><strong>{activeAgent?.display_name ?? "Loading agent"}</strong><p>{activeAgent?.description ?? "Preparing the local harness."}</p></div><span className="online-badge">Ready</span></div>
              {agentPickerOpen && <div className="agent-select-list">{agents.map((agent) => <button key={agent.agent_id} onClick={() => chooseAgent(agent)} className={`agent-option ${agent.agent_id === state?.active_agent_id ? "agent-option--selected" : ""}`}><span>{agent.icon || agent.display_name[0]}</span>{agent.display_name}{agent.agent_id === state?.active_agent_id && <Check size={15} />}</button>)}</div>}
              <div className="capabilities">{activeAgent?.capabilities.slice(0, 4).map((capability) => <span key={capability}>{capability}</span>)}</div>
            </section>

            <section className="rail-section">
              <div className="rail-heading"><span>WORKING CONTEXT</span><span className="muted-label">{sources.length} sources</span></div>
              <div className="source-select"><label>Repository</label><select value={selectedSource?.name ?? ""} onChange={(event) => { const source = sources.find((item) => item.name === event.target.value); if (source) void chooseSource(source); }}><option value="" disabled>Select a connected source</option>{sources.map((source) => <option key={source.name} value={source.name}>{source.label}</option>)}</select></div>
              <div className="branch-select"><label>Branch</label><select value={state?.selected_branch ?? selectedSource?.default_branch ?? ""} disabled={!selectedSource} onChange={(event) => void chooseBranch(event.target.value)}><option value="" disabled>Select a branch</option>{selectedSource?.branches.map((branch) => <option key={branch} value={branch}>{branch}</option>)}</select></div>
            </section>

            <section className="rail-section">
              <div className="rail-heading"><span>SESSION PULSE</span><Clock3 size={15} /></div>
              <div className="session-pulse"><span className={`state-dot state-dot--${stateTone(activeSession?.state)}`} /><div><strong>{activeSession?.state_label ?? "Awaiting work"}</strong><p>{compactSessionName(activeSession?.name)}</p></div></div>
              {sessionNeedsApproval && <button className="approval-button" onClick={() => void approvePlan()}><Check size={15} />Approve plan</button>}
              {activeSession?.url && <a className="text-link" href={activeSession.url} target="_blank" rel="noreferrer">Open in Jules <ArrowUpRight size={14} /></a>}
            </section>

            <section className="rail-section rail-section--journal">
              <div className="rail-heading"><span>RECENT SIGNAL</span><span className="muted-label">live</span></div>
              {events.slice(0, 3).map((event) => <div className="signal-row" key={event.id}><span /><div><strong>{event.type.replace(".", " ")}</strong><p>{event.summary}</p></div></div>)}
              {events.length === 0 && <p className="no-signals">Actions will appear as local journal entries.</p>}
            </section>
          </aside>
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value, hint, danger }: { label: string; value: number; hint: string; danger?: boolean }) {
  return <div className={`metric ${danger ? "metric--danger" : ""}`}><span>{label}</span><strong>{value.toString().padStart(2, "0")}</strong><em>{hint}</em></div>;
}

function ChatBubble({ message, agent }: { message: ChatMessage; agent?: Agent }) {
  const isOperator = message.role === "operator";
  const isSystem = message.role === "system";
  const label = isOperator ? "You" : isSystem ? "Harness" : agent?.display_name ?? "Agent";
  return <article className={`message ${isOperator ? "message--operator" : ""} ${isSystem ? "message--system" : ""}`}><div className="message-avatar">{isOperator ? "V" : isSystem ? "!" : agent?.icon || "J"}</div><div><div className="message-meta"><strong>{label}</strong><span>{formatTime(message.timestamp)}</span></div><p>{message.text}</p></div></article>;
}

function SessionCard({ session, active, onApprove, onAttach }: { session: Session; active: boolean; onApprove: () => void; onAttach: () => void }) {
  const needsApproval = session.state?.toLowerCase().includes("plan");
  return <article className={`session-card ${active ? "session-card--active" : ""}`}><div className="session-card__top"><span className={`state-dot state-dot--${stateTone(session.state)}`} /><div><strong>{session.title || compactSessionName(session.name)}</strong><p>{session.source_name?.split("/").slice(-2).join("/") || "No source selected"} · {session.starting_branch || "default branch"}</p></div><span className="state-pill">{session.state_label}</span></div><div className="session-card__footer"><span>{compactSessionName(session.name)}</span><div className="session-card__actions">{active ? <span className="attached-pill">Attached</span> : <button className="mini-button" onClick={onAttach}>Attach</button>}{needsApproval && <button className="mini-button" onClick={onApprove}>Approve plan</button>}{session.url && <a href={session.url} target="_blank" rel="noreferrer">Open <ArrowUpRight size={13} /></a>}</div></div></article>;
}

function DiagnosticCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone?: "sage" | "rose" }) {
  return <article className="diagnostic-card"><span>{label}</span><strong className={tone ? `tone--${tone}` : ""}>{value}</strong><p>{detail}</p></article>;
}

function TimelineEvent({ event }: { event: EventRecord }) {
  return <article className="timeline-event"><div className="timeline-event__rail"><span /></div><div><div className="timeline-event__meta"><strong>{event.type.replace(".", " ")}</strong><span>{formatTime(event.timestamp)}</span></div><p>{event.summary}</p>{event.session_name && <code>{compactSessionName(event.session_name)}</code>}</div></article>;
}

function EmptyCard({ title, text }: { title: string; text: string }) {
  return <div className="empty-card"><Bot size={21} /><strong>{title}</strong><p>{text}</p></div>;
}
