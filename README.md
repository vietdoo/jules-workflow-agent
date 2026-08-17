# Jules Workflow Agent

A production-oriented asynchronous Telegram bot that gives a chat-based control surface for the **Jules REST API**. Users can send coding tasks, continue an existing Jules session, choose a connected GitHub repository and branch, inspect activities, approve plans, open the native Jules session, list remote sessions, and safely end sessions from Telegram.

The application targets Python 3.10+, `python-telegram-bot` 20+, `aiohttp`, and `python-dotenv`. Jules is an asynchronous API: a request creates or updates a session quickly, while agent work is reported through activities. The bot therefore combines request methods with activity polling and a polished inline-keyboard UI.

> Jules REST API is currently documented as an experimental `v1alpha` API. Endpoint names and payloads may change as the API evolves. Keep the API base URL configurable and review the official reference before production upgrades.[1]

## Capabilities

| Capability | Telegram experience | Jules API operation |
| --- | --- | --- |
| Start or continue work | Send any ordinary text message | `POST /sessions`, `POST /sessions/{session}:sendMessage` |
| Browse repositories | `Repositories` button or `/sources` | `GET /sources` |
| Choose a branch | Repository selection presents branch buttons | `sourceContext.githubRepoContext.startingBranch` |
| Inspect a session | `Session status` button or `/session` | `GET /sessions/{session}` |
| Review progress | `Activities` button or `/activities` | `GET /sessions/{session}/activities` |
| Approve a plan | Confirmation-protected `Approve plan` button | `POST /sessions/{session}:approvePlan` |
| Browse remote sessions | `All sessions` button or `/sessions` | `GET /sessions` |
| Attach another session | Select a session from the list | Local chat-to-session association |
| End work | Confirmation-protected `End session` button | `DELETE /sessions/{session}` |
| Open the native Jules UI | `Open in Jules` URL button | Session `url` returned by Jules |
| Automation | Optional environment setting | `automationMode`, for example `AUTO_CREATE_PR` |

The bot covers the documented REST capabilities that are appropriate for a Telegram interface. **Connecting a new GitHub repository to Jules is not available through the REST API**; sources are read-only and must first be connected in the Jules web application.[1] [2]

## User interface

`/start` opens the main agent card with inline buttons for a new session, live status, repositories, activities, all sessions, and help. Selecting a repository presents its available branches. Session cards display the state, title, identifier, source, branch, and native Jules URL where available. Long agent replies are split at Telegram's message limit, and callback errors are acknowledged without exposing API keys or raw credentials.

The available commands are:

| Command | Purpose |
| --- | --- |
| `/start` | Open the main Jules Workflow Agent menu. |
| `/help` | Display usage and API limitation guidance. |
| `/sources` | List connected repositories and select a source and branch. |
| `/session` | Display the active session's current metadata and controls. |
| `/activities` | Render the recent Jules activity timeline, including plans and artifacts. |
| `/sessions` | List recent remote Jules sessions and attach one to the current chat. |
| `/new` | Confirm and clear the chat's local session association without deleting the remote Jules session. |

## Project layout

```text
jules-workflow-agent/
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── jules_client.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   └── message_handlers.py
│   └── main.py
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
| `src/config.py` | Loads `.env`, validates required values, parses positive numeric settings and booleans, and exposes immutable runtime configuration. |
| `src/api/jules_client.py` | Implements authenticated async HTTP calls, pagination, source parsing, session lifecycle, plan approval, activity retrieval, artifact-aware polling, and typed errors. |
| `src/handlers/message_handlers.py` | Implements commands, message forwarding, per-chat state, inline keyboards, callback routing, session cards, source/branch selection, activity timelines, and safe error presentation. |
| `src/main.py` | Builds the Telegram application, registers command/callback handlers, and selects polling or webhook mode. |
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

Leave `WEBHOOK_URL` blank for local long polling. The Telegram application and Jules HTTP client close during application shutdown.

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

For a new chat task, the handler selects the chat's repository and branch if configured, calls `POST /sessions`, stores the returned session resource name, and polls activities until a new agent message or failure is observed. Follow-up messages record known activity names, call `:sendMessage`, and poll only for new activity. Status and activity buttons always retrieve fresh data from Jules rather than relying only on cached UI text.

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

The current process stores chat-to-session associations and UI selections in memory. A restart clears only those local associations; remote Jules sessions remain available through `/sessions`. For multiple replicas or durable ownership, replace `ChatSessionStore` with a shared database or key-value store.

## Validation

Compile all Python modules without making network calls:

```bash
python -m compileall -q src
```

The configuration loader fails fast when required credentials are missing or numeric and boolean settings are invalid. The API client converts unsuccessful HTTP responses, transport failures, timeouts, and malformed JSON into typed exceptions. The Telegram layer returns concise user-safe messages while keeping operational details in logs.

## Security and limitations

Never commit `.env`, Jules API keys, or Telegram tokens. Store production credentials in Render's encrypted environment settings. Do not place the Jules API key in a Telegram message, callback payload, URL, or client-side application.

The bot can expose the Jules REST API's documented sessions, activities, sources, plan approval, session deletion, and automation fields. It cannot reproduce features that are not exposed by the REST API, including connecting a new GitHub repository through the bot. Users can use the `Open in Jules` button to access the native Jules website for web-only operations.

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

## References

[1]: https://jules.google/docs/api/reference/ "Jules REST API Quickstart and API Concepts"
[2]: https://jules.google/docs/api/reference/sources "Jules REST API Sources Reference"
[3]: https://jules.google/docs/api/reference/sessions "Jules REST API Sessions Reference"
[4]: https://jules.google/docs/api/reference/activities "Jules REST API Activities Reference"
