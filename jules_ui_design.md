# Jules workflow and Telegram UI design

## API coverage

The client will cover the documented v1alpha operations needed by the bot: list/get sources, create/list/get/delete sessions, send messages, approve plans, and list/get activities with pagination. Session creation will expose prompt, title, source context, starting branch, plan approval, and automation mode. Activity parsing will recognize agent/user messages, plans, progress updates, completion/failure, and artifacts.

## Telegram controls

The bot will use inline keyboards and callback queries so users can operate Jules without memorizing every command:

| Control | Action |
| --- | --- |
| New session | Clear the local chat-to-session link after confirmation. |
| My session / Status | Fetch the live Jules session and render state, title, URL, source, branch, and action buttons. |
| Activities | Fetch recent activities and render a compact timeline with plans, progress, messages, completion/failure, and artifact indicators. |
| Repositories | List connected Jules sources and let the user select a repository and branch for the next new session. |
| Approve plan | Call `:approvePlan` only when the session is awaiting approval; a confirmation callback prevents accidental approval. |
| End session | Confirm, call the documented DELETE session endpoint, and clear the local chat state. |
| Help | Show command and button guidance. |

## State model

The process will retain a per-chat state containing the current Jules session name, selected source/branch, last session payload, pending prompt, and source options. This remains intentionally in-memory for the single-process Render deployment described by the project; the README will explain how to replace it with durable storage for replicas.

## Presentation

The Telegram UI will use a consistent card format with headings, state badges, concise metadata, URLs, and action buttons. Long Jules text will be split safely under Telegram's message limit. Callback errors will be acknowledged and shown as user-safe messages without leaking API keys or raw upstream failures.
