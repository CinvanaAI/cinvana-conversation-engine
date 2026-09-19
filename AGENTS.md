# Conversation Engine Working Scope

## Implementation Scope

For architecture, audit, cleanup, or code changes, inspect only:

- README.md
- AGENTS.md
- pyproject.toml
- conversation_engine/
- docs/
- tests/

Do not use Storage/Generated Artifacts/Message Pipeline as an implementation dependency. It may be consulted only when the owner explicitly requests legacy semantic comparison or migration work.

## Runtime Boundary

Do not enumerate or inspect Runtime recursively during implementation work. Runtime contains private messages, database content, live workspaces, Suggestions, and Quarantine bundles.

Tests must use disposable roots and must never fall through to the real Runtime path.

Published folders in Runtime/Workspaces/STAGE/To_Do are externally active. Never edit, replace, rename, or delete one except through the implemented service transition or an explicit owner-directed operational repair.

## System Boundary

Feeders and consumers are outside Conversation Engine. Do not add Discord, delivery, acknowledgment, consumer queues, API-token workflows, or automation integration to this subsystem unless the owner explicitly changes the boundary.

Do not add retries or defer states for bounded item failures. Complete external quarantine is the failure contract. Database or filesystem unavailability is a system stop.
