# Jules API Response-Lifecycle Notes

Verified on 2026-08-17 against the official Jules REST reference.

The documented activity-list endpoint is `GET /v1alpha/{parent=sessions/*}/activities`; it accepts an optional `pageSize` and returns an `activities` array with an optional pagination token. The documented follow-up endpoint is `POST /v1alpha/{session=sessions/*}:sendMessage` with a JSON body containing the required `prompt` string. A successful follow-up response has an empty body, so clients must independently poll session activities for later agent messages rather than expecting an immediate reply.

The local client already targets the documented URL shapes. The Studio repair should therefore focus on eventual-consistency handling after session creation, durable persistence of the new session association before polling, and displaying delayed agent activity without treating the initial asynchronous gap as a failed user interaction.

## Sources

1. [Jules API: `sessions.activities.list`](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions.activities/list)
2. [Jules API: `sessions.sendMessage`](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions/sendMessage)
3. [Jules API: `sessions.create`](https://developers.google.com/jules/api/reference/rest/v1alpha/sessions/create)
