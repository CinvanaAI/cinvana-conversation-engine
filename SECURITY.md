# Security and private-state boundary

Conversation Engine is designed to hold exact messages, transformations,
contracts, workspaces, rejection bundles, suggestions, reports, and SQLite
state. None of that belongs in the public repository.

- `Runtime/` is ignored except for its boundary note and empty placeholders.
- Tests use disposable roots and must never fall through to a live runtime.
- Treat returned workspace content as untrusted until contract validation.
- Do not add feeder, consumer, Discord, delivery, token, or automation concerns
  to this subsystem without an explicit architecture change.
- Do not publish a runtime snapshot merely because the source is public-safe.

The local HTML diagram atlas fetches Mermaid from jsDelivr when opened. Review
that external dependency if offline or high-assurance documentation is needed.

