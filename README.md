# Jules Telegram Bot

A production-oriented asynchronous Telegram bot that forwards ordinary Telegram messages to the Jules API and sends the agent's response back to the originating chat. The application is written for Python 3.10+, uses `python-telegram-bot` 20+, communicates with Jules through `aiohttp`, and loads configuration with `python-dotenv`.

The official Jules API is an asynchronous REST API. It exposes sources, sessions, and activities, and authenticates requests with the `X-Goog-Api-Key` header. The default client configuration in this project targets `https://jules.googleapis.com/v1alpha`.[1] [2]

## Features

The bot provides `/start` and `/help` commands. Every non-command text message is forwarded to Jules asynchronously. Messages from the same Telegram chat reuse one Jules session in memory, allowing follow-up messages to retain context. Per-chat locks prevent overlapping requests from corrupting the conversation flow, while the client polls Jules activities until a new agent response is available or the configured timeout is reached.

The process supports long polling for local development and webhook mode for Render or another HTTPS-capable host. Startup validates `TELEGRAM_BOT_TOKEN` and `JULES_API_KEY`, and the repository includes a Render Blueprint, an environment template, a minimal dependency list, and documented operational steps.

## Project layout

```text
my-telegram-bot/
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
├── render.yaml
├── requirements.txt
└── README.md
```

| Module | Responsibility |
| --- | --- |
| `src/config.py` | Loads `.env`, validates required values, parses numeric settings, and exposes immutable runtime configuration. |
| `src/api/jules_client.py` | Encapsulates asynchronous HTTP requests, Jules authentication, error conversion, session creation, message sending, activity polling, and response extraction. |
| `src/handlers/message_handlers.py` | Implements Telegram commands, forwards user messages, maintains in-memory per-chat session associations, and handles Telegram's message-size limit. |
| `src/main.py` | Builds the Telegram application and selects polling or webhook mode based on `WEBHOOK_URL`. |
| `render.yaml` | Defines a Render web service with Python build and start commands plus secret environment variables. |

## Prerequisites

You need Python 3.10 or newer, a Telegram bot token created through [BotFather](https://t.me/BotFather), and a Jules API key created in the Jules web app settings. Jules API keys must remain server-side and should never be committed to Git or embedded in client-side code.[1]

For the official Jules API, connect a GitHub repository to Jules and set `JULES_SOURCE` to the returned source resource name. The official API documentation shows source resources in the form `sources/github/OWNER/REPOSITORY`, and session creation accepts a prompt, source context, and optional automation mode.[1]

## Configuration

Copy the template and edit it locally:

```bash
cp .env.example .env
```

The two mandatory values are `TELEGRAM_BOT_TOKEN` and `JULES_API_KEY`. The remaining values have safe development defaults, except that webhook mode is enabled only when `WEBHOOK_URL` is non-empty.

| Variable | Required | Description |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes | Token issued by Telegram BotFather. |
| `JULES_API_KEY` | Yes | Jules API key sent as `X-Goog-Api-Key`. |
| `JULES_API_URL` | No | API base URL; defaults to `https://jules.googleapis.com/v1alpha`. |
| `JULES_SOURCE` | Recommended for official Jules | Connected Jules source such as `sources/github/owner/repository`. |
| `JULES_STARTING_BRANCH` | No | Starting branch for a new Jules session; defaults to `main`. |
| `JULES_AUTOMATION_MODE` | No | Optional Jules value such as `AUTO_CREATE_PR`. |
| `WEBHOOK_URL` | No | Public HTTPS base URL. If set, the bot uses webhook mode at `/telegram/webhook`. |
| `PORT` | No | Listening port; defaults to `8080` and is normally supplied by Render. |
| `WEBHOOK_SECRET_TOKEN` | No | Telegram webhook secret token. Render generates one in `render.yaml`. |
| `JULES_TIMEOUT_SECONDS` | No | Maximum duration for one HTTP request; defaults to `60`. |
| `JULES_POLL_INTERVAL_SECONDS` | No | Delay between Jules activity polls; defaults to `2`. |
| `JULES_REPLY_TIMEOUT_SECONDS` | No | Maximum wait for an agent response; defaults to `120`. |
| `LOG_LEVEL` | No | Python logging level; defaults to `INFO`. |

If you are integrating a Jules-compatible custom API instead of the official Jules service, set `JULES_API_URL` to that service's base URL and verify that its session, send-message, and activities endpoints follow the same resource shape. The client is deliberately isolated in one module so a different request schema can be adapted without changing Telegram handlers.

## Local development

Create and activate a virtual environment, install dependencies, and run the module from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m src.main
```

When `WEBHOOK_URL` is blank, the application starts Telegram long polling and does not require a public URL. To stop the bot, press `Ctrl+C`. The Telegram application and the Jules HTTP session are closed through the application's shutdown hook.

You can verify that every Python module compiles without importing external services:

```bash
python -m compileall -q src
```

The configuration loader fails fast with a clear message if a required environment variable is missing or a numeric setting is invalid.

## Render deployment

The included `render.yaml` describes a Render Web Service. A web service is appropriate when Telegram should deliver updates through a public webhook. The deployment process is:

1. Push this directory to a Git repository.
2. In Render, create a new Blueprint and select the repository.
3. Set `TELEGRAM_BOT_TOKEN`, `JULES_API_KEY`, and `WEBHOOK_URL` in the service environment. `WEBHOOK_URL` must be the public HTTPS base URL of the Render service, such as `https://my-telegram-bot.onrender.com`; do not include `/telegram/webhook`, because the application appends that path.
4. Keep `WEBHOOK_SECRET_TOKEN` enabled. The Blueprint generates it automatically, and the application passes it to Telegram when registering the webhook.
5. Deploy and inspect the service logs. The startup log states whether webhook or polling mode was selected and prints the registered webhook URL without printing secrets.

The Render service uses:

```text
Build command: pip install -r requirements.txt
Start command: python -m src.main
```

Render supplies a `PORT` value to web services. The application binds to `0.0.0.0` and uses that value, which is required for platform routing. If you prefer a Render Background Worker, use the same build and start commands but leave `WEBHOOK_URL` empty; the bot will use long polling. A worker does not need a public HTTP endpoint, but it cannot receive Telegram webhooks.

> **Important:** Run only one polling worker or one webhook instance for a bot token unless you intentionally coordinate update delivery. Multiple independent instances can compete for Telegram updates or duplicate processing.

## Jules request lifecycle

For the first message in a chat, the handler creates a Jules session and polls `sessions/{id}/activities`. For later messages, it first records the existing activity names, calls `:sendMessage`, and polls until it observes a new activity containing agent text. The official REST reference documents the session creation, retrieval, listing, and message-sending endpoints used by this workflow.[2]

```text
Telegram message
       │
       ▼
Telegram handler ── create session or sendMessage ──► Jules REST API
       ▲                                             │
       └──────────── new agent activity ◄────────────┘
```

Jules sessions are stored only in process memory. A process restart clears chat-to-session associations, and multiple replicas do not share them. For a multi-instance deployment or durable conversation history, replace `ChatSessionStore` with a shared database or key-value store and add an explicit session ownership strategy.

## Security and operations

Never commit `.env`, API keys, or bot tokens. The included `.gitignore` excludes `.env` and common Python-generated files. Store production credentials in Render's encrypted environment settings. Logs contain chat identifiers and operational errors but do not log message bodies or credentials.

The client applies request timeouts, converts transport failures and unsuccessful API responses into typed exceptions, and avoids exposing upstream error details to Telegram users. Users receive a short retry message while full diagnostic context remains in server logs. The polling timeout is configurable because Jules tasks may be asynchronous and can take longer than a normal HTTP request.

For production use, consider adding a persistent session store, rate limiting per chat, an allowlist of Telegram user IDs, structured log shipping, and a health check endpoint if your hosting platform requires one. These additions are intentionally not included as unused dependencies or speculative infrastructure in the minimal application requested here.

## Troubleshooting

### The bot exits with a configuration error

Copy `.env.example` to `.env`, ensure both required credentials are non-empty, and check that numeric settings contain positive values. The application must be started from the project root so Python can resolve the `src` package with `python -m src.main`.

### Jules returns HTTP 400 when creating a session

For the official API, confirm that the Jules GitHub integration is installed and that `JULES_SOURCE` exactly matches one of the source resource names returned by the Jules sources endpoint. The API documentation recommends listing available sources before creating a session.[1]

### The bot says Jules timed out

The request may still be running in Jules. Increase `JULES_REPLY_TIMEOUT_SECONDS` if longer responses are normal, or inspect the Jules session directly. The bot keeps the newly created session association in memory, so a later message can continue that session while the process remains alive.

### Render starts but Telegram does not deliver messages

Confirm that `WEBHOOK_URL` is the public HTTPS service URL without the webhook path, that the Render service is running, and that the logs show webhook mode. Also verify that the bot token is not being used by another polling process and that Telegram can reach the Render URL over HTTPS.

## References

[1]: https://developers.google.com/jules/api "Jules API | Google for Developers"
[2]: https://developers.google.com/jules/api/reference/rest "Jules API REST Reference | Google for Developers"
