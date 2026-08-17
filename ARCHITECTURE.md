# Agent Harness Architecture

## Goal

`jules-workflow-agent` is organized as a Telegram delivery adapter around an application-level **agent harness**. The harness owns agent selection, conversation routing, session state boundaries, and concurrency control. Agent-specific HTTP details remain behind an adapter contract, so adding another provider does not require rewriting Telegram handlers.

## Layers

| Layer | Responsibility | Examples |
| --- | --- | --- |
| Domain | Stable contracts and data concepts | `AgentDescriptor`, `AgentAdapter`, `AgentReply` |
| Application | Use-case orchestration and routing | `AgentRegistry`, `AgentHarness`, chat state store |
| Infrastructure | External systems and provider adapters | Jules REST client and `JulesAgent` |
| Interface | Telegram commands, callbacks, formatting, and keyboards | `src/handlers/message_handlers.py` |
| Composition root | Dependency wiring and lifecycle | `src/main.py` |

## Agent contract

Every agent adapter exposes a stable descriptor and asynchronous operations for creating or continuing work, retrieving sessions and activities, listing sources, approving plans, and deleting sessions. The Telegram adapter never needs to know whether the active agent is Jules, another REST provider, or a local automation service.

To add an agent:

1. Implement `AgentAdapter` in `src/infrastructure/agents/`.
2. Register it in `build_application()` with an `AgentRegistry`.
3. Add provider-specific settings to configuration only when required.
4. Add adapter-level contract tests; existing Telegram routing and UI remain reusable.

## State and scale

The initial state store is process-local and implements a small interface. It is intentionally replaceable with Redis, Postgres, or another shared store without changing handlers or agent adapters. The store owns chat-to-agent selection, active session references, source/branch choices, and short-lived callback option maps. Per-chat locks prevent duplicate concurrent actions in one process.

For horizontal scaling, replace the in-memory store with a shared implementation, use a distributed lock keyed by chat ID, and add an explicit session ownership policy. The application layer already keeps provider state separate from Telegram update handling, which makes that migration bounded.

## Operational principles

Secrets stay in environment variables and are never included in callback payloads. Callback payloads contain short opaque keys mapped to state-store entries. Provider errors are converted to safe user messages and structured log context. The composition root owns adapter lifecycle and closes all network clients during shutdown.
