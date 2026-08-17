# Jules API audit notes

Sources reviewed on 2026-08-17:

- https://jules.google/docs/api/reference/
- https://jules.google/docs/api/reference/sessions

Key findings:

1. The REST API is currently documented under `/v1alpha` at `https://jules.googleapis.com/v1alpha` and uses the `X-Goog-Api-Key` header.
2. The API is asynchronous and models work with Sources, Sessions, and Activities.
3. `POST /sessions` accepts required `prompt`; optional `title`, `sourceContext`, `requirePlanApproval`, and `automationMode`. The documented `automationMode` example is `AUTO_CREATE_PR`.
4. A created Session includes resource `name`, `id`, `state`, `url`, `createTime`, and `updateTime` in the documented example.
5. The sessions reference includes list, get, delete, send-message, and approve-plan operations. Send message is `POST /sessions/{sessionId}:sendMessage` with `{ "prompt": "..." }`; approve plan is `POST /sessions/{sessionId}:approvePlan` with an empty JSON object.
6. `requirePlanApproval=true` requires an explicit approve-plan action; otherwise plans are auto-approved.
7. The current bot only implements create session, send message, list activities, and activity polling. It does not yet expose source listing, session list/get/delete, plan approval, session state display, activity history, artifacts, or interactive Telegram controls.

8. Activities can be listed with `GET /sessions/{sessionId}/activities` and paginated with `pageSize` and `pageToken`; individual activities can be fetched by ID.
9. Documented activity types include planGenerated, planApproved, userMessaged, agentMessaged, progressUpdated, sessionCompleted, and sessionFailed. Artifacts include changeSet/gitPatch, bashOutput, and media.
10. Sources are connected GitHub repositories and are read-only through the API. `GET /sources` supports pageSize, pageToken, and AIP-160 filter. Source responses expose owner, repo, privacy, default branch, and branches.
11. The UI can safely expose source discovery and branch selection, but cannot create/connect repositories through the API; that remains a Jules web-interface action.
