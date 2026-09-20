# Follow one source through the engine

Run `python -m examples.offline_demo` from the installed checkout. The [complete output](../examples/result.json) is an actual run with invented source text and authored worker replies. The disposable root is removed when the example exits. No current vault is read and no model is called.

The input is one envelope for `synthetic-workshop`, position 1: “Morgan will review the fixture.” The example initializes the database, bootstraps the seed definitions, publishes a live-ready mapping index with the title `Workshop`, and explicitly unpauses intake in that disposable vault. A newly initialized real vault begins paused and needs its own reviewed definitions.

1. **Accept the source.** `EngineService.intake.accept` validates the envelope and retains an immutable source revision. Task planning captures the exact definitions and stage contracts it needs.
2. **Materialize worker requests.** `WorkspaceManager` writes `request.json`, `message.txt` and stage-specific instructions/context. The recorded `input_files` lists show what each synthetic worker actually received. These inputs are checked against captured hashes on return; a worker cannot quietly change its assignment.
3. **Return the three authored replies.** Mapping returns `response.json` with `discord_public_indexing: ["Workshop"]`. Public Safety returns `response.json` with `decision: "Public"` and no redactions. Original Versions returns only `version_1.txt`, containing “Review the fixture.” The trace preserves the exact file contents.
4. **Validate and resolve.** The example moves only its own disposable workspaces from `To_Do` to `Done`, then runs the ordinary service loop. Captured validators check response shape and semantics. Promotion also checks whether the task still matches current dependencies; a returned file is not automatically a current result.
5. **Inspect all six heads.** Mapping retains its title assignment; Public Safety retains redaction-form output; Original Versions retains compact version data. Public Versions points to Original Versions because the fixture was marked Public. Discord Original points to the short source. Discord Public points to that current result. All six are complete without six worker calls.

The single authored shorter version establishes a semantic floor in this fixture: missing levels 2–9 refer to level 1, so resolving private level 9 returns “Review the fixture.” Structural/length validation is not an independent proof that an authored summary preserved every meaning.

## Failure and recovery boundaries

The [stage contracts](stage_contracts.md) are authoritative for allowed return files and exact validation. Invented mapping titles, overlapping/out-of-bounds redactions, changed request inputs or invalid version files do not become current output. [Workspace tests](../tests/test_workspaces_and_contracts.py) and [quarantine tests](../tests/test_quarantine.py) exercise those failure boundaries with disposable fixtures.

Bounded item failures use complete external quarantine and the service halt policy. Quarantine is not a retry queue; repair and normal-path reintroduction are described in [operations](operations.md). Filesystem/database unavailability is a system stop. A worker must not repair failure by manually editing active workspace inputs or manufacturing current database rows.

This example demonstrates source intake, handoff, validation, pointer reuse and resolution. It does not demonstrate model judgment, a real archive migration, encrypted storage, feeder scheduling or external publication. Runtime content, worker outputs, backups and rejection evidence remain local/private.
