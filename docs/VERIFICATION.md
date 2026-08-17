# Local Harness Verification Notes

## 2026-08-17: Workspace visual pass

The Next.js orchestration studio was inspected in a desktop browser preview. The Claude-inspired warm control-room layout rendered with its persistent navigation, dashboard metrics, central conversation canvas, source selector, active-agent card, session pulse, and responsive action controls.

The exposed browser preview intentionally remained in its loading state because the preview itself is served from a public proxy while the FastAPI control plane is deliberately bound to `127.0.0.1`. In the supported local runtime, both services run on the same machine through `scripts/run_local.py`; FastAPI health and dashboard requests returned HTTP 200 during the concurrent-service check.

This behavior preserves the documented local-only security boundary. Do not weaken `WEB_API_HOST` or CORS defaults merely to make an external preview connect to local agent credentials.
