CREATE TABLE messages (
    message_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    created_at TEXT NOT NULL,
    UNIQUE (chat_id, position)
);

CREATE TABLE source_revisions (
    revision_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    envelope_json TEXT NOT NULL,
    envelope_sha256 TEXT NOT NULL UNIQUE,
    destination_rel_path TEXT NOT NULL,
    speaker TEXT NOT NULL,
    message_timestamp TEXT NOT NULL,
    message_text TEXT NOT NULL,
    message_sha256 TEXT NOT NULL,
    char_count INTEGER NOT NULL CHECK (char_count >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (message_id, revision_number)
);

CREATE TABLE message_heads (
    message_id TEXT PRIMARY KEY REFERENCES messages(message_id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL UNIQUE REFERENCES source_revisions(revision_id)
);

CREATE TABLE ingested_sources (
    envelope_sha256 TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL REFERENCES source_revisions(revision_id),
    first_filename TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);

CREATE TABLE definition_versions (
    definition_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, name, version),
    UNIQUE (kind, name, payload_sha256)
);

CREATE TABLE definition_heads (
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    definition_id TEXT NOT NULL REFERENCES definition_versions(definition_id),
    PRIMARY KEY (kind, name)
);

CREATE TABLE contract_versions (
    contract_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    worker_policy TEXT NOT NULL CHECK (worker_policy IN ('model', 'deterministic', 'dynamic')),
    validator_key TEXT NOT NULL,
    validator_version INTEGER NOT NULL CHECK (validator_version > 0),
    workspace_spec_json TEXT NOT NULL,
    output_schema_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (stage, version),
    UNIQUE (stage, contract_sha256)
);

CREATE TABLE contract_heads (
    stage TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES contract_versions(contract_id)
);

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL REFERENCES source_revisions(revision_id),
    stage TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('model', 'deterministic')),
    contract_id TEXT NOT NULL REFERENCES contract_versions(contract_id),
    desired_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'planned', 'preparing', 'published', 'returned',
            'validated', 'promoted', 'superseded'
        )
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (message_id, stage, desired_sha256)
);

CREATE TABLE task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    dependency_kind TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    dependency_sha256 TEXT NOT NULL,
    PRIMARY KEY (task_id, dependency_kind, dependency_id)
);

CREATE TABLE stage_targets (
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (message_id, stage)
);

CREATE TABLE stage_results (
    result_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    task_id TEXT UNIQUE REFERENCES tasks(task_id),
    contract_id TEXT REFERENCES contract_versions(contract_id),
    output_json TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE result_dependencies (
    result_id TEXT NOT NULL REFERENCES stage_results(result_id) ON DELETE CASCADE,
    dependency_kind TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    dependency_sha256 TEXT NOT NULL,
    PRIMARY KEY (result_id, dependency_kind, dependency_id)
);

CREATE TABLE stage_heads (
    message_id TEXT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    result_id TEXT NOT NULL REFERENCES stage_results(result_id),
    promoted_at TEXT NOT NULL,
    PRIMARY KEY (message_id, stage)
);

CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id) ON DELETE CASCADE,
    folder_name TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('preparing', 'published', 'returned', 'absorbed')
    ),
    request_json TEXT,
    request_sha256 TEXT,
    input_manifest_json TEXT,
    input_manifest_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE dirty_messages (
    message_id TEXT PRIMARY KEY REFERENCES messages(message_id) ON DELETE CASCADE,
    reasons_json TEXT NOT NULL,
    marked_at TEXT NOT NULL
);

CREATE TABLE controls (
    target TEXT PRIMARY KEY,
    paused INTEGER NOT NULL CHECK (paused IN (0, 1)),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE quarantine_records (
    quarantine_id TEXT PRIMARY KEY,
    message_id TEXT,
    source_path TEXT,
    state TEXT NOT NULL CHECK (state IN ('preparing', 'published', 'completed')),
    reason_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    bundle_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message_id TEXT,
    subject_id TEXT,
    details_json TEXT NOT NULL
);

CREATE INDEX idx_tasks_dispatch
    ON tasks(kind, status, stage, created_at);
CREATE INDEX idx_tasks_message_stage
    ON tasks(message_id, stage, created_at);
CREATE INDEX idx_results_message_stage
    ON stage_results(message_id, stage, created_at);
CREATE INDEX idx_workspaces_state
    ON workspaces(state, created_at);
CREATE INDEX idx_quarantine_completed
    ON quarantine_records(completed_at);
CREATE INDEX idx_revisions_chat_position
    ON source_revisions(message_id, revision_number);
CREATE INDEX idx_messages_chat_position
    ON messages(chat_id, position);

CREATE TRIGGER immutable_source_revision
BEFORE UPDATE ON source_revisions
BEGIN
    SELECT RAISE(ABORT, 'source revisions are immutable');
END;

CREATE TRIGGER immutable_definition
BEFORE UPDATE ON definition_versions
BEGIN
    SELECT RAISE(ABORT, 'definition versions are immutable');
END;

CREATE TRIGGER immutable_contract
BEFORE UPDATE ON contract_versions
BEGIN
    SELECT RAISE(ABORT, 'contract versions are immutable');
END;

CREATE TRIGGER immutable_result
BEFORE UPDATE ON stage_results
BEGIN
    SELECT RAISE(ABORT, 'stage results are immutable');
END;

CREATE TRIGGER immutable_result_dependency
BEFORE UPDATE ON result_dependencies
BEGIN
    SELECT RAISE(ABORT, 'result dependencies are immutable');
END;

CREATE TRIGGER immutable_audit
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE VIEW current_message_revisions AS
SELECT messages.message_id, messages.chat_id, messages.position,
       revisions.revision_id, revisions.revision_number,
       revisions.destination_rel_path, revisions.speaker,
       revisions.message_timestamp, revisions.message_text,
       revisions.message_sha256, revisions.char_count
FROM messages
JOIN message_heads ON message_heads.message_id = messages.message_id
JOIN source_revisions revisions
  ON revisions.revision_id = message_heads.revision_id;

CREATE VIEW current_stage_results AS
SELECT heads.message_id, heads.stage, heads.result_id,
       results.output_json, results.output_sha256,
       results.contract_id, heads.promoted_at
FROM stage_heads heads
JOIN stage_results results ON results.result_id = heads.result_id;