# Local Harness Verification Notes

## 2026-08-17: Workspace visual pass

The Next.js orchestration studio was inspected in a desktop browser preview. The Claude-inspired warm control-room layout rendered with its persistent navigation, dashboard metrics, central conversation canvas, source selector, active-agent card, session pulse, and responsive action controls.

The exposed browser preview intentionally remained in its loading state because the preview itself is served from a public proxy while the FastAPI control plane is deliberately bound to `127.0.0.1`. In the supported local runtime, both services run on the same machine through `scripts/run_local.py`; FastAPI health and dashboard requests returned HTTP 200 during the concurrent-service check.

This behavior preserves the documented local-only security boundary. Do not weaken `WEB_API_HOST` or CORS defaults merely to make an external preview connect to local agent credentials.

## 2026-08-17: Temporary sandbox exposure

The FastAPI health endpoint was reachable through the temporary sandbox proxy and returned the expected local harness status. The Studio now keeps browser traffic on its own origin and uses a Next.js server-side rewrite for `/api/*` to the loopback FastAPI process. This avoids embedding a public API URL in client code, makes the temporary proxy usable for the dashboard, and preserves the control plane's local binding. The configured sandbox host is also explicitly permitted for Next.js development-origin resources.

This is a temporary preview mechanism, not a production-access boundary. Any production deployment must apply authentication and restrict the Studio's public visibility before exposing Jules credentials or local-workspace controls.

### Post-restart browser verification

The public Studio was refreshed after the harness restart. Its active-agent card resolved to **Jules**, the loading-state canvas changed to the ready-to-orchestrate empty state, and the repository selector populated with 76 connected Jules sources. The FastAPI dashboard, source list, session list, event list, and event-stream WebSocket each completed through the same-origin Studio route.
