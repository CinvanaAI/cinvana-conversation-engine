# Architecture Contract

## Authority

One SQLite vault is the sole live-state authority. Filesystem folders are transport boundaries for new envelopes, model work, Suggestions, quarantine, backups, and reports. They are not canonical message state.

The service is the sole coordinator. Separate definition publishers may write through the same transactional repository, but consumers use read-only connections.

## Identity And Migration Safety

A vault has a required singleton identity containing a UUID, subsystem identifier, and format version. Every normal connection validates identity read-only before opening a writable connection.

Initialization refuses an unknown nonempty SQLite file. Status and read-only state inspection never initialize, migrate, or modify a database.

Ordered SQL migrations have immutable names and SHA-256 checksums. Opening a vault fails if an applied migration differs from the packaged source, if the database contains an unknown future migration, or if a recognized vault needs an explicit upgrade.

## Immutable Records And Heads

Immutable records:

- source revisions;
- definition versions;
- stage contract versions;
- task dependency captures;
- stage results and result dependencies;
- audit events.

Mutable coordination records:

- message source heads;
- definition and contract heads;
- desired stage targets;
- promoted stage heads;
- task and workspace lifecycle state;
- durable dirty-message planning work;
- pause controls;
- resumable quarantine transitions.

Changing a mutable head never edits its referenced immutable record.

## Desired State Versus Live State

Each message and stage may have both:

- a promoted stage head, which is live;
- a desired task target, which represents the newest captured dependency set.

Planning computes a canonical SHA-256 over the source revision, captured contract, stage payload, and exact dependencies. The same desired contract is idempotent.

A replacement target does not demote the promoted head. Promotion creates one immutable result and, only when the returning task is still desired, advances the stage head in the same transaction. Otherwise the valid result is marked superseded until the retention sweep can safely reclaim it.

Supersession is transitional, not permanent history. After service work, a
reference-aware sweep starts from current stage heads, desired task targets,
and active workspaces, then follows upstream result dependencies transitively.
It deletes every unreachable result and task. An old result therefore remains
available while a live downstream result or active replacement still depends
on it, but is reclaimed after the dependency cascade converges. At rest, each
message and stage has one current result rather than an accumulated run
history. Immutable definition and contract versions are retained separately.

## Durable Fanout

Publishing a source revision marks only the bounded Mapping-neighbor range plus the source message dirty.

Publishing a definition or stage contract marks every message dirty in the same transaction as the new head. Upstream result promotion marks its message dirty in the promotion transaction.

Dirty records are primary-key coalesced. The planner always rebuilds desired targets from current heads, so any number of updates before planning resolves to the newest state. This mechanism works in an already-running process and after restart; it is not startup-only reconciliation.

## Intake

A service cycle enumerates and parses the Unprocessed tree once, orders candidates, and accepts a bounded slice. It never rescans the remaining tree once per item.

For one valid envelope, the following share one SQLite transaction:

- message identity;
- immutable source revision;
- source head;
- idempotency record;
- Intake result and head;
- initial desired stage tasks;
- audit event.

The source file is removed only after commit. A crash before removal is harmless because the canonical envelope hash makes the next acceptance idempotent.

Invalid envelopes never enter message state and are quarantined externally.

## Workspaces

Every model task receives a globally unique workspace ID and folder name that is never reused.

Publication boundaries:

1. Insert a preparing workspace record.
2. Materialize exact captured inputs under Workspaces/Drafts.
3. Store the request and input manifest hashes.
4. Atomically rename the folder into Workspaces/STAGE/To_Do.
5. Mark the workspace published.

Published inputs and request.json are immutable. Reconciliation verifies their captured hashes. A live preparing, published, or returned workspace blocks publication of every other task for the same message and stage.

When a model moves the whole folder from Workspaces/STAGE/To_Do to Workspaces/STAGE/Done, the service atomically claims it into Workspaces/STAGE/Claimed. Validation uses the task's captured contract ID, response schema, validator key/version, exact definitions, source, and dependency IDs. It never substitutes current heads.

After successful validation, an optional plain-text `Suggestions` file is atomically captured as one JSON file under `Runtime/Suggestions/STAGE`. Its stable filename combines the message and workspace IDs, and its payload records message, stage, task, workspace, timestamp, content hash, and suggestion text. This keeps Suggestions externally reviewable without per-workspace directories or automatic definition changes.

## Failure Model

A bounded item failure creates a resumable external bundle under `Runtime/Rejections/STAGE/QUARANTINE_ID` containing:

- the original source envelope;
- a complete database snapshot;
- every workspace folder for the message;
- every captured Suggestion for the message, including captures whose obsolete task metadata has already been reclaimed;
- a reason record;
- a verified SHA-256 manifest and COMPLETE marker.

Only after the final bundle verifies does SQLite remove the message and all operational rows. Completed quarantine metadata retains only the rejection bundle/fuse identity; private snapshots, source paths, message IDs, and message audit rows are removed from SQLite.

There is no item retry or defer state. Reintroduction copies the isolated source envelope into normal Unprocessed Intake after the system has been repaired.

The sixth completed quarantine in a rolling sixty minutes writes an external persistent halt marker. A database or filesystem failure stops the service because the system cannot truthfully complete quarantine under those conditions.

## Restart Boundaries

Reconciliation completes infrastructure transitions only:

- accepted source still present after commit;
- incomplete unpublished draft;
- fully prepared draft before atomic publication;
- To_Do moved to Done before database claim;
- Done moved to Claimed before returned state;
- promoted workspace folder left after commit;
- partially materialized quarantine bundle;
- published quarantine bundle before database removal;
- durable dirty planning after any process exit.

Restart reconciliation never retries failed model semantics.

## Consumer Boundary

The database provides read-only current-state views. It stores no consumer queue, delivery attempt, acknowledgment, Discord operation, or compiled consumer document. Consumer-specific behavior remains outside the subsystem.

The canonical database record is a recipe graph. Source text and genuinely
authored reduction versions are content. Public Safety redactions and Discord
ranges are transformations. Reused results and fill-forward levels are
pointers. The same deterministic resolver creates temporary workspace inputs
and read-only consumer projections; resolved public-safe text and Discord chunk
text are not canonical database fields.
