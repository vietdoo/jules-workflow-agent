# Jules Workflow Agent

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A production-oriented asynchronous Telegram bot that gives a chat-based control surface for the **Jules REST API**. Users can send coding tasks, continue an existing Jules session, choose a connected GitHub repository and branch, inspect activities, approve plans, open the native Jules session, list remote sessions, and safely end sessions from Telegram.

The application targets Python 3.10+, `python-telegram-bot` 20+, `aiohttp`, and `python-dotenv`. Jules is an asynchronous API: a request creates or updates a session quickly, while agent work is reported through activities. The bot therefore combines request methods with activity polling and a polished inline-keyboard UI.

> Jules REST API is currently documented as an experimental `v1alpha` API. Endpoint names and payloads may change as the API evolves. Keep the API base URL configurable and review the official reference before production upgrades.[1]

## Table of Contents
- [Features](#features)
- [User interface](#user-interface)
- [Quick Start](#quick-start)
- [Project layout](#project-layout)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Local development](#local-development)
- [Full local harness: Telegram + browser studio](#full-local-harness-telegram--browser-studio)
- [Render deployment](#render-deployment)
- [Jules request lifecycle](#jules-request-lifecycle)
- [Validation](#validation)
- [Security and limitations](#security-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [References](#references)

## Features

| Capability | Telegram experience | Jules API operation |
| --- | --- | --- |
| **Start or continue work** | Send any ordinary text message | `POST /sessions`, `POST /sessions/{session}:sendMessage` |
| **Browse repositories** | `Repositories` button or `/sources` | `GET /sources` |
| **Choose a branch** | Repository selection presents branch buttons | `sourceContext.githubRepoContext.startingBranch` |
| **Inspect a session** | `Session status` button or `/session` | `GET /sessions/{session}` |
| **Review progress** | `Activities` button or `/activities` | `GET /sessions/{session}/activities` |
| **Approve a plan** | Confirmation-protected `Approve plan` button | `POST /sessions/{session}:approvePlan` |
| **Browse remote sessions**| `All sessions` button or `/sessions` | `GET /sessions` |
| **Attach another session**| Select a session from the list | Local chat-to-session association |
| **End work** | Confirmation-protected `End session` button | `DELETE /sessions/{session}` |
| **Open the native Jules UI**| `Open in Jules` URL button | Session `url` returned by Jules |
| **Automation** | Optional environment setting | `automationMode`, for example `AUTO_CREATE_PR` |

The bot covers the documented REST capabilities that are appropriate for a Telegram interface. **Connecting a new GitHub repository to Jules is not available through the REST API**; sources are read-only and must first be connected in the Jules web application.[1] [2]

## User interface

`/start` opens the main agent card with a consistent workspace layout: active agent, selected repository, conversation status, and inline controls for new tasks, live status, repositories, activities, sessions, and help. `/agents` or the **Agents** button opens the provider picker. Selecting a repository presents its available branches. Session cards display state, title, identifier, source, branch, and native provider URL where available. Long agent replies are split at Telegram's message limit, no-op edits are ignored, and callback errors are acknowledged without exposing API keys or raw credentials.

The available commands are:

| Command | Purpose |
| --- | --- |
| `/start` | Open the main Jules Workflow Agent menu. |
| `/help` | Display usage, architecture, and API limitation guidance. |
| `/agents` | List registered agents and choose the active provider. |
| `/sources` | List connected repositories and select a source and branch. |
| `/session` | Display the active session's current metadata and controls. |
| `/activities` | Render the recent Jules activity timeline, including plans and artifacts. |
| `/sessions` | List recent remote Jules sessions and attach one to the current chat. |
| `/new` | Confirm and clear the chat's local session association without deleting the remote Jules session. |

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone <your-repo-url>
   cd jules-workflow-agent
   ```
2. **Install dependencies:**
   ```bash
   make install
   ```
3. **Configure the environment:**
   ```bash
   cp .env.example .env
   # Edit .env and set TELEGRAM_BOT_TOKEN and JULES_API_KEY
   ```
4. **Run the local harness:**
   ```bash
   make dev
   ```

## Project layout

```text
jules-workflow-agent/
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── domain/
│   │   └── agent.py                 # Provider-neutral agent contract
│   ├── application/
│   │   └── harness.py               # Registry, routing, state, and use cases
│   ├── infrastructure/
│   │   └── agents/jules_agent.py    # Jules adapter implementation
│   ├── api/
│   │   └── jules_client.py          # Async Jules REST client
│   ├── handlers/
│   │   └── message_handlers.py      # Telegram presentation adapter
│   └── main.py                       # Composition root and lifecycle
├── tests/
│   └── test_harness.py
├── ARCHITECTURE.md
├── .env.example
├── .gitignore
├── jules_api_audit_notes.md
├── jules_ui_design.md
├── render.yaml
├── requirements.txt
└── README.md
```

| Module | Responsibility |
| --- | --- |
| `src/domain/agent.py` | Defines the provider-neutral adapter contract, normalized agent descriptors, and reply envelopes. |
| `src/application/harness.py` | Owns agent registration, chat routing, per-chat state boundaries, serialized requests, and provider-neutral use cases. |
| `src/infrastructure/agents/jules_agent.py` | Adapts Jules-specific REST operations to the shared agent contract. New providers should implement the same adapter interface here. |
| `src/config.py` | Loads `.env`, validates required values, parses positive numeric settings and booleans, and exposes immutable runtime configuration. |
| `src/api/jules_client.py` | Implements authenticated async HTTP calls, pagination, source parsing, session lifecycle, plan approval, activity retrieval, artifact-aware polling, and typed errors. |
| `src/handlers/message_handlers.py` | Implements the Telegram presentation adapter: commands, inline keyboards, agent picker, session cards, source/branch selection, activity timelines, and safe error presentation. |
| `src/main.py` | Acts as the composition root, registers adapters and handlers, and selects polling or webhook mode. |
| `tests/test_harness.py` | Runs dependency-free unit tests for registry routing, agent switching, session boundaries, and request serialization. |
| `ARCHITECTURE.md` | Defines the extension contract and scale path for additional agents and durable state. |
| `render.yaml` | Defines Render deployment settings and secret environment variables. |

## Prerequisites

You need Python 3.10 or newer, a Telegram bot token created with [BotFather](https://t.me/BotFather), and a Jules API key generated in [Jules Settings](https://jules.google.com/settings#api). Jules API keys must remain server-side and should never be committed to Git or embedded in client-side code.[1]

For repository-aware coding tasks, connect the GitHub repository to Jules through the Jules web application. Then use the bot's **Repositories** button or `/sources` command to confirm the source resource name and choose a branch. The API currently supports reading connected GitHub sources, not creating them.[2]

## Configuration

Copy the template and edit it locally:

```bash
cp .env.example .env
```

The two required values are `TELEGRAM_BOT_TOKEN` and `JULES_API_KEY`.

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Token issued by Telegram BotFather. |
| `JULES_API_KEY` | Yes | Jules API key sent in the `X-Goog-Api-Key` header. |
| `JULES_API_URL` | No | Defaults to `https://jules.googleapis.com/v1alpha`. |
| `JULES_SOURCE` | No | Optional default source, such as `sources/github/owner/repository`. The UI can override it for new sessions. |
| `JULES_STARTING_BRANCH` | No | Default branch for new sessions; defaults to `main`. The UI can override it. |
| `JULES_REQUIRE_PLAN_APPROVAL` | No | When `true`, the bot exposes a confirmation-protected plan approval action. Defaults to `false`. |
| `JULES_AUTOMATION_MODE` | No | Optional Jules automation value, such as `AUTO_CREATE_PR`. |
| `AGENT_DEFAULT_ID` | No | Agent selected for new chats; defaults to `jules`. The ID must be registered in the composition root. |
| `WEB_API_HOST` | No | FastAPI control-plane bind address; defaults to `127.0.0.1`. |
| `WEB_API_PORT` | No | FastAPI control-plane port; defaults to `8090`. |
| `WEB_CORS_ORIGINS` | No | Comma-delimited local browser origins; defaults to the local Next.js origins. |
| `LOCAL_DATA_DIR` | No | Ignored directory for local JSON state, JSONL events, Markdown journals, and logs; defaults to `runtime`. |
| `WEBHOOK_URL` | No | Public HTTPS base URL. When set, the bot uses `/telegram/webhook`; when blank, it uses long polling. |
| `PORT` | No | Listening port; defaults to `8080` and is normally supplied by Render. |
| `WEBHOOK_SECRET_TOKEN` | No | Telegram webhook secret token. |
| `JULES_TIMEOUT_SECONDS` | No | Maximum duration for one HTTP request; defaults to `60`. |
| `JULES_POLL_INTERVAL_SECONDS` | No | Delay between activity polls; defaults to `2`. |
| `JULES_REPLY_TIMEOUT_SECONDS` | No | Maximum wait for a text response; defaults to `120`. |
| `LOG_LEVEL` | No | Python logging level; defaults to `INFO`. |

## Local development

Create a virtual environment, install dependencies, and start the bot from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m src.main
```

Leave `WEBHOOK_URL` blank for local long polling. The Telegram application and all registered agent adapters close during application shutdown. Run the local architecture tests with:

```bash
python -m unittest discover -s tests -v
```

## Full local harness: Telegram + browser studio

The repository also includes a **full local harness**. It runs Telegram, a provider-neutral FastAPI control plane, and a Next.js orchestration studio as three local processes that use the same agent contracts and local audit directory.

```text
Telegram ────────┐
                 ├── AgentHarness ── Jules adapter ── Jules REST API
Next.js Studio ─ FastAPI control plane ┘
                         │
                         └── runtime/ (JSON state + JSONL events + Markdown journal)
```

Run all three interfaces together:

```bash
make install
make dev
```

Open `http://127.0.0.1:3000` for the orchestration studio and `http://127.0.0.1:8090/docs` for local API documentation. The studio provides a warm, Claude-inspired workflow workspace with chat composer, agent selection, repository and branch controls, session overview, activity timeline, local transcript access, and dashboard operational signals. The FastAPI service remains bound to loopback by default; the browser does not receive a Jules or Telegram credential.

The local event store writes every browser action and response to ignored files below `runtime/`:

```text
runtime/
├── state/conversations.json
├── events/YYYY-MM-DD.jsonl
├── journals/YYYY-MM-DD.md
└── logs/agent-harness.log
```

Read [LOCAL_HARNESS.md](LOCAL_HARNESS.md) for the runtime topology, endpoints, event persistence model, security boundary, individual process commands, and the path to replace local files with durable shared services. Use `make check` to run the complete offline Python and Next.js validation suite.

## Render deployment

The included `render.yaml` defines a Render Web Service suitable for Telegram webhooks. Deploy it as follows:

1. Create a Render Blueprint from this repository.
2. Set `TELEGRAM_BOT_TOKEN` and `JULES_API_KEY` in the service environment.
3. Set `WEBHOOK_URL` to the public HTTPS base URL of the service, without `/telegram/webhook`.
4. Keep the generated webhook secret enabled.
5. Inspect logs for the startup mode and registered webhook URL.

The service uses:

```text
Build command: pip install -r requirements.txt
Start command: python -m src.main
```

A Render Background Worker can use the same commands with `WEBHOOK_URL` empty, which selects polling. Run only one polling worker or one webhook instance for a bot token unless update delivery is coordinated.

## Jules request lifecycle

For a new chat task, Telegram delegates to the application harness rather than calling Jules directly. The harness selects the active adapter, serializes requests per chat, stores provider-neutral conversation state, and delegates the prompt to the adapter. The Jules adapter selects the chat's repository and branch if configured, calls `POST /sessions`, stores the returned session resource name, and polls activities until a new agent message or failure is observed. Follow-up messages call `:sendMessage` and poll only for new activity. Status and activity buttons retrieve fresh provider data rather than relying only on cached UI text.

Adding another agent should require a new adapter implementing the contract in `src/domain/agent.py`, registration in `src/main.py`, and provider-specific tests. Telegram commands, callback routing, state locking, and the active-agent UI remain unchanged.

```text
Telegram task
     │
     ▼
Bot UI ── create session / send message ──► Jules REST API
  │                                             │
  │◄──── status, plan, progress, artifacts ◄────┘
  │
  ├── approve plan
  ├── inspect activities
  ├── open native Jules URL
  └── delete session after confirmation
```

The current process stores chat-to-agent associations, session links, and UI selections in memory behind the `ChatStateStore` port. A restart clears only those local associations; remote sessions remain available through the provider's session list. For multiple replicas or durable ownership, implement the same store contract with Redis or a database, then inject it into `AgentHarness`; no Telegram handler changes are required.

## Validation

Compile all Python modules without making network calls:

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

The configuration loader fails fast when required credentials are missing or numeric and boolean settings are invalid. The API client converts unsuccessful HTTP responses, transport failures, timeouts, and malformed JSON into typed exceptions. The Telegram layer returns concise user-safe messages while keeping operational details in logs.

## Security and limitations

Never commit `.env`, Jules API keys, or Telegram tokens. Store production credentials in Render's encrypted environment settings. Do not place the Jules API key in a Telegram message, callback payload, URL, or client-side application.

The bot can expose the Jules REST API's documented sessions, activities, sources, plan approval, session deletion, and automation fields through the Jules adapter. It cannot reproduce features that are not exposed by the REST API, including connecting a new GitHub repository through the bot. Users can use the provider URL button to access the native Jules website for web-only operations. The in-memory state store is intentionally single-process; use a shared implementation before running multiple replicas.

## Troubleshooting

### The bot exits with a configuration error

Copy `.env.example` to `.env`, set both required credentials, and confirm that numeric settings are positive and boolean values use `true` or `false`. Start from the repository root with `python -m src.main`.

### No repositories appear

Install or connect the Jules GitHub integration in the Jules web application, then retry `/sources`. The API only lists connected sources; it does not create them.[2]

### Jules returns HTTP 400 when creating a session

Confirm that the selected source is an exact source resource returned by `/sources`, that the branch exists, and that the Jules GitHub integration can access the repository. Temporarily clear `JULES_SOURCE` if you want to test a source-less request.

### The approval button is unavailable

The button is shown when the session state indicates plan work or when `JULES_REQUIRE_PLAN_APPROVAL=true`. Jules only accepts `:approvePlan` when a plan is pending. Refresh the status and activity views before retrying.

### Jules timed out

The task may still be running. Increase `JULES_REPLY_TIMEOUT_SECONDS`, inspect `/session` and `/activities`, or open the native Jules session URL. The bot keeps the local association while the process remains alive.

### Render starts but Telegram does not deliver messages

Confirm that `WEBHOOK_URL` is the public HTTPS URL without the webhook path, that the service is running, and that no separate polling process is using the same bot token.

## Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md) for details on how to get started.

This project uses the [MIT License](LICENSE).

## References

[1]: https://jules.google/docs/api/reference/ "Jules REST API Quickstart and API Concepts"
[2]: https://jules.google/docs/api/reference/sources "Jules REST API Sources Reference"
[3]: https://jules.google/docs/api/reference/sessions "Jules REST API Sessions Reference"
[4]: https://jules.google/docs/api/reference/activities "Jules REST API Activities Reference"
