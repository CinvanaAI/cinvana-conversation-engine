# Operations Contract

## Service Ownership

The continuous service acquires Runtime/State/service.lock. A second service process fails immediately. Definition and contract publication use short SQLite transactions and may occur while the service runs.

## Controls

Persistent database controls:

- intake;
- deterministic;
- model_dispatch;
- stage:mapping;
- stage:public_safety;
- stage:original_versions;
- stage:public_versions;
- stage:discord_original;
- stage:discord_public.

Planning continues while execution or dispatch is paused. Returned work always absorbs unless the external global halt marker exists.

Drain is an audited Intake pause. Unpause and resume require a new reason.

Intake cannot be unpaused while the current Mapping Index is explicitly non-live or has no titles.

## Definition And Contract Changes

Definitions and contracts are immutable publications. Publishing equal content is idempotent. Publishing changed content advances only its head and persists dirty-message fanout.

Do not edit database rows or packaged seed JSON to make an operational update. Publish a JSON payload through the CLI.

A contract update must retain every validator implementation needed by still-live older workspaces. Removing an old validator version is a breaking data-compatibility change.

## Suggestions

A model may return one optional plain-text `Suggestions` file. After successful validation, the service captures it atomically as `Runtime/Suggestions/STAGE/MESSAGE_ID_WORKSPACE_ID.json`. The JSON records the suggestion text and hash plus its message, stage, task, workspace, and capture time. There are no per-workspace directories. Suggestions never mutate definitions, tasks, or current results.

Definition review is a human operation. Publishing an approved definition automatically creates replacement work through durable dependency fanout.

## Quarantine And Rejections

Quarantine is the verified isolation transaction and halt policy. Completed evidence is published under `Runtime/Rejections/STAGE/QUARANTINE_ID`. It is a bug-finding boundary, not a retry queue.

Verify:

```powershell
python -B -m conversation_engine quarantine-verify QUARANTINE_ID
```

After repairing the system, reintroduce through normal Intake:

```powershell
python -B -m conversation_engine quarantine-reintroduce QUARANTINE_ID
```

Do not manually move a rejected workspace back into To_Do. Reintroduction must prove the normal path now succeeds.

If quarantine publication itself cannot complete, stop and repair the database/filesystem. Do not invent an alternate rejection location.

## Backup And Recovery

SQLite backup uses the connection-level backup API so WAL state is captured consistently. A sidecar manifest records size, SHA-256, timestamp, and vault identity.

Definitions/contracts also support an exact portable export. Exact restore is allowed only into an initialized empty vault and verifies every definition hash, contract hash, and validator version.

Runtime backups and exports contain private information. Keep them outside Git and do not publish them.

## Encryption

The initial vault is plaintext by explicit decision. Future SQLCipher work belongs behind db.py and must include key-loss, wrong-key, rekey, encrypted backup/restore, interrupted transaction, and clean-machine recovery tests before conversion.

## Migration Boundary

No existing message, custody record, workspace, old database row, automation, or entry point has been migrated.

A future migration must be separately reviewed. The intended principle remains normal-path proof:

1. Admit exact original envelopes through Unprocessed.
2. Correlate old and new immutable source identities.
3. Let the new engine create unique workspaces.
4. Return compatible prior model outputs through those exact folders.
5. Let ordinary validation, promotion, and quarantine decide every result.
6. Update definitions afterward to trigger full dependency-driven reprocessing when approved.

No migration action should bypass validation or directly manufacture promoted results.
