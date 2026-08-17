# Jules Workflow Local Harness

## Purpose

This repository runs one local control plane for the existing Telegram bot and a browser-based orchestration studio. Both interfaces call the same agent harness and provider adapters. The control plane remains local-first: it uses no database server and keeps a readable audit trail on disk.

## Runtime topology

```text
Telegram Bot ───────┐
                    ├── AgentHarness ── JulesAgent ── Jules API
Next.js Studio ── FastAPI Control Plane ┘
                         │
                         └── runtime/
                              ├── state/conversations.json
                              ├── events/YYYY-MM-DD.jsonl
                              ├── journals/YYYY-MM-DD.md
                              └── logs/agent-harness.log
```

The Telegram process and the FastAPI process each create their own HTTP client, which avoids sharing an event loop across processes. They can, however, read and append to the same local state and audit files. The local event store uses append-only JSONL for machine processing and an accompanying Markdown journal for human auditability.

## Component boundaries

| Layer | Responsibility |
| --- | --- |
| `src/domain` | Provider-neutral agent contracts and normalized source/session models. |
| `src/application` | Conversation routing, a replaceable state-store port, and the web control-plane use cases. |
| `src/infrastructure` | Jules adapter, JSON state persistence, Markdown/JSON event storage, and provider HTTP clients. |
| `apps/api` | FastAPI adapter that exposes HTTP and WebSocket controls without provider-specific logic. |
| `apps/web` | Next.js local studio consuming the FastAPI contract. |
| `src/handlers` | Telegram presentation adapter. |

## Local persistence contract

`LOCAL_DATA_DIR` defaults to `runtime`. It is deliberately ignored by Git because it can contain real prompts, agent replies, session references, and operational logs. Every web action writes structured JSONL plus a Markdown journal entry; the logging setup also writes Python application logs to `runtime/logs/agent-harness.log`.

The JSON state store is suitable for a single workstation and a small number of local processes. It writes atomically, but it is not a distributed database. For multi-machine or multi-replica deployment, keep the `StateStore` and event-store interfaces and replace their implementations with a transactional store and distributed lock.

## Run modes

The intended local command is `python scripts/run_local.py` or `make dev`. It loads `.env`, starts FastAPI, Next.js, and Telegram together, and terminates every child process on Ctrl+C or when any service exits. Each service can also run independently for debugging.

| Service | Command | Local address |
| --- | --- | --- |
| FastAPI control plane | `python -m apps.api` | `http://127.0.0.1:8090` |
| Next.js studio | `pnpm --dir apps/web dev` | `http://localhost:3000` |
| Telegram | `python -m src.main` | Telegram polling or configured webhook |

## Browser workflow

Open `http://127.0.0.1:3000` after the runner reports the web service as ready. The studio is intentionally a second interface to the same harness rather than a separate agent implementation. It provides a dashboard, active-agent picker, source and branch selection, remote-session view, activity stream, Markdown timeline, local transcripts, and a composer that delegates work to the shared `AgentHarness`.

The FastAPI OpenAPI contract is available at `http://127.0.0.1:8090/docs`. The browser connects to its event stream at `/api/events/stream`; it can rehydrate the full local audit history from `/api/events` after a refresh.

## First run

```bash
cp .env.example .env
# Populate TELEGRAM_BOT_TOKEN and JULES_API_KEY only in .env.
make install
make dev
```

Use `make check` before pushing a change. The command compiles Python modules, runs offline unit/contract tests, type-checks Next.js, and produces a production Next.js build.

## Environment additions

| Variable | Default | Purpose |
| --- | --- | --- |
| `WEB_API_HOST` | `127.0.0.1` | FastAPI bind interface. Keep loopback for local-only usage. |
| `WEB_API_PORT` | `8090` | FastAPI control-plane port. |
| `WEB_UI_PORT` | `3000` | Next.js development port used by `scripts/run_local.py`. |
| `WEB_CORS_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` | Comma-delimited allowed browser origins. |
| `LOCAL_DATA_DIR` | `runtime` | Ignored root for JSON state, JSONL events, Markdown journals, and logs. |

## Extending with another agent

Create an adapter that implements `AgentAdapter`, translate its provider models into `AgentSource` and `AgentSession`, and register it in `src/bootstrap.py`. The FastAPI and Next.js interfaces discover registered agents through the harness; they should not import a new provider SDK directly.

## Security notes

The browser UI is designed for `127.0.0.1` by default. Do not expose the FastAPI server to a public network until an authentication layer, TLS, and a shared durable state store are configured. Keep `.env` and `runtime/` out of Git. The Next.js app never receives Jules or Telegram credentials; it only calls the local FastAPI control plane.
