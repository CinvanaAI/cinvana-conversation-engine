export const implementationDiagrams = [
{
  id: "16",
  title: "SQLite Vault Data Model",
  category: "Implementation",
  code: String.raw`erDiagram
    VAULT_IDENTITY {
      int singleton PK
      text vault_uuid UK
      text subsystem
      int format_version
    }
    SCHEMA_MIGRATIONS {
      int version PK
      text name
      text sha256
    }
    MESSAGES {
      text message_id PK
      text chat_id
      int position
    }
    SOURCE_REVISIONS {
      text revision_id PK
      text message_id FK
      int revision_number
      text envelope_sha256 UK
      text message_sha256
      text message_text
    }
    MESSAGE_HEADS {
      text message_id PK
      text revision_id FK
    }
    INGESTED_SOURCES {
      text envelope_sha256 PK
      text message_id FK
      text revision_id FK
    }
    DEFINITION_VERSIONS {
      text definition_id PK
      text kind
      text name
      int version
      text payload_sha256
    }
    DEFINITION_HEADS {
      text kind PK
      text name PK
      text definition_id FK
    }
    CONTRACT_VERSIONS {
      text contract_id PK
      text stage
      int version
      text validator_key
      int validator_version
      text contract_sha256
    }
    CONTRACT_HEADS {
      text stage PK
      text contract_id FK
    }
    TASKS {
      text task_id PK
      text message_id FK
      text revision_id FK
      text stage
      text kind
      text contract_id FK
      text desired_sha256
      text status
    }
    TASK_DEPENDENCIES {
      text task_id FK
      text dependency_kind
      text dependency_id
      text dependency_sha256
    }
    STAGE_TARGETS {
      text message_id FK
      text stage
      text task_id FK
    }
    STAGE_RESULTS {
      text result_id PK
      text message_id FK
      text task_id FK
      text stage
      text output_sha256
    }
    RESULT_DEPENDENCIES {
      text result_id FK
      text dependency_kind
      text dependency_id
      text dependency_sha256
    }
    STAGE_HEADS {
      text message_id FK
      text stage
      text result_id FK
    }
    WORKSPACES {
      text workspace_id PK
      text task_id FK
      text folder_name UK
      text state
      text request_sha256
      text input_manifest_sha256
    }
    DIRTY_MESSAGES {
      text message_id PK
      text reasons_json
    }
    CONTROLS {
      text target PK
      int paused
      text reason
    }
    QUARANTINE_RECORDS {
      text quarantine_id PK
      text message_id
      text state
      text bundle_name UK
    }
    AUDIT_EVENTS {
      int event_id PK
      text message_id
      text event_type
      text subject_id
    }
    MESSAGES ||--o{ SOURCE_REVISIONS : owns
    MESSAGES ||--|| MESSAGE_HEADS : selects_current
    SOURCE_REVISIONS ||--o{ INGESTED_SOURCES : identifies
    MESSAGES ||--o{ TASKS : plans
    SOURCE_REVISIONS ||--o{ TASKS : captured_by
    CONTRACT_VERSIONS ||--o{ TASKS : governs
    TASKS ||--o{ TASK_DEPENDENCIES : captures
    MESSAGES ||--o{ STAGE_TARGETS : desires
    TASKS ||--o| STAGE_TARGETS : targeted_by
    TASKS ||--o| WORKSPACES : materializes
    TASKS ||--o| STAGE_RESULTS : produces
    STAGE_RESULTS ||--o{ RESULT_DEPENDENCIES : preserves
    MESSAGES ||--o{ STAGE_RESULTS : histories
    MESSAGES ||--o{ STAGE_HEADS : exposes
    STAGE_RESULTS ||--o| STAGE_HEADS : promoted_as
    MESSAGES ||--o| DIRTY_MESSAGES : queues
    DEFINITION_VERSIONS ||--o| DEFINITION_HEADS : selected_by
    CONTRACT_VERSIONS ||--o| CONTRACT_HEADS : selected_by
    MESSAGES ||--o{ AUDIT_EVENTS : traces`
},
{
  id: "19",
  title: "Vault Initialization, Verification, Backup, and Recovery",
  category: "Operations",
  code: String.raw`flowchart TD
    INIT["conversation_engine init"] --> EXISTS{"Database file exists and nonempty?"}
    EXISTS -- "Yes" --> ID{"Recognized vault identity?"}
    ID -- "No" --> REFUSE["Refuse without mutation"]
    ID -- "Yes" --> LEDGER["Verify applied migration names + SHA-256"]
    EXISTS -- "No" --> CORE["Create vault_identity and schema_migrations"]
    CORE --> UUID["Assign unique vault UUID<br/>subsystem conversation-engine<br/>format version 1"]
    UUID --> MIG["Apply ordered SQL migrations transactionally<br/>Record immutable checksums"]
    LEDGER --> UP{"Pending recognized migrations?"}
    UP -- "Yes" --> EXPLICIT["Require explicit upgrade"]
    UP -- "No" --> CONNECT["Open connection"]
    MIG --> BOOT["Bootstrap definitions and contracts<br/>Leave Intake paused"]
    BOOT --> CONNECT
    CONNECT --> MODE{"Operation"}
    MODE -- "verify" --> VERIFY["Read-only identity validation<br/>Migration ledger check<br/>PRAGMA integrity_check<br/>Foreign-key check"]
    MODE -- "backup" --> BAK["SQLite connection backup API<br/>Captures WAL-consistent state"]
    BAK --> MAN["Write sidecar manifest<br/>bytes + SHA-256 + timestamp + vault identity"]
    MODE -- "definitions-export" --> EXP["Portable exact bundle<br/>All definition/contract versions + heads"]
    MODE -- "definitions-restore" --> EMPTY{"Initialized vault empty?"}
    EMPTY -- "No" --> RREF["Refuse restore"]
    EMPTY -- "Yes" --> RHASH["Verify every definition hash<br/>contract hash<br/>validator availability"]
    RHASH --> REST["Restore exact IDs, versions, and heads<br/>Pause Intake for approval"]
    READ["status and state commands"] --> RO["Read-only connections<br/>Never initialize, migrate, or modify"]
    ENC["Encryption intentionally deferred<br/>Centralized later boundary: db.py"] --- CONNECT
    PRIV["Backups and exports may contain private data<br/>Runtime remains outside Git"] --- MAN`
},
{
  id: "21",
  title: "Code Ownership and Call Map",
  category: "Implementation",
  code: String.raw`flowchart TD
    CLI["cli.py<br/>Commands and operation routing"] --> CFG["config.py<br/>Runtime path map + stage list"]
    CLI --> DB["db.py<br/>Vault identity, migrations,<br/>connections, transactions, backup"]
    CLI --> BOOT["bootstrap.py<br/>Seed, definition/contract publish,<br/>export and exact restore"]
    CLI --> LOCK["singleton.py<br/>One continuous coordinator"]
    CLI --> SERVICE["service.py<br/>Ordered service cycle"]
    CLI --> Q["quarantine.py<br/>Halt, isolation, verification,<br/>reintroduction"]
    DB --> SQL["migrations/001_core.sql<br/>Tables, triggers, views, indexes"]
    BOOT --> SEED["seed/bootstrap.json<br/>Instructions, Mapping Index placeholder,<br/>six stage contracts"]
    BOOT --> REPO["repository.py<br/>All transactional state transitions"]
    SERVICE --> INTAKE["intake.py<br/>Bounded admission"]
    SERVICE --> PLAN["planner.py<br/>Desired tasks and dependencies"]
    SERVICE --> EXEC["executor.py<br/>Deterministic pointer tasks"]
    SERVICE --> WS["workspaces.py<br/>Publish, reconcile, claim, absorb"]
    SERVICE --> Q
    INTAKE --> ENV["envelope.py<br/>Exact input validation, normalization,<br/>hashing, scan and ordering"]
    INTAKE --> REPO
    PLAN --> REPO
    EXEC --> REPO
    WS --> REPO
    Q --> REPO
    WS --> VAL["validators.py<br/>Mapping, Safety, Versions,<br/>Discord range semantics"]
    EXEC --> SCHEMA["schema.py<br/>Normalized JSON schema validation"]
    VAL --> SCHEMA
    VAL --> REPO
    REPO --> DEFVAL["definition_validation.py<br/>Instruction and Mapping Index shapes"]
    REPO --> IDS["ids.py<br/>Globally unique typed IDs"]
    REPO --> JSON["jsonutil.py<br/>Canonical JSON, hashes,<br/>atomic writes, manifests"]
    Q --> JSON
    WS --> JSON
    TESTS["tests/<br/>44 disposable-vault acceptance tests"] -. "verifies all boundaries" .-> SERVICE
    DOCS["docs/<br/>Architecture, stage contracts,<br/>operations contract"] -. "declares intended behavior" .-> SERVICE
    AG["AGENTS.md<br/>Strict subsystem and Runtime boundaries"] --- DOCS`
},
{
  id: "22",
  title: "Runtime Filesystem Map",
  category: "Implementation",
  code: String.raw`flowchart TD
    ROOT["Conversation Engine/Runtime<br/>Private transport and operational state<br/>Ignored by Git except placeholders"]
    ROOT --> STATE["State/<br/>conversation_engine.sqlite3<br/>WAL and SHM sidecars<br/>service.lock<br/>HALTED.json when halted"]
    ROOT --> UNP["Unprocessed/<br/>Exact incoming JSON envelopes"]
    ROOT --> WORK["Workspaces/"]
    ROOT --> SUG["Suggestions/STAGE/<br/>Unique JSON captures after validation"]
    ROOT --> TEMP["Temp/<br/>Resumable quarantine construction"]
    ROOT --> QUAR["Rejections/STAGE/<br/>Verified complete isolation bundles"]
    ROOT --> REP["Reports/<br/>Explicit generated reports only"]
    ROOT --> BAK["Backups/<br/>SQLite-consistent vault backups<br/>and definition exports"]
    WORK --> DRAFT["Drafts/<br/>Unpublished atomic workspace construction"]
    WORK --> TODO["To_Do/<br/>Published immutable model inputs"]
    WORK --> DONE["Done/<br/>Whole folders returned by model"]
    WORK --> CLAIM["Claimed/<br/>Service-owned returned folders"]
    UNP --> STATE
    STATE --> DRAFT
    DRAFT -- "atomic rename" --> TODO
    TODO -- "external model moves folder" --> DONE
    DONE -- "service atomic claim" --> CLAIM
    CLAIM -- "valid result promotes" --> STATE
    CLAIM -- "optional successful suggestion" --> SUG
    CLAIM -- "bounded failure" --> TEMP
    UNP -- "invalid envelope" --> TEMP
    TODO -- "tamper or disappearance" --> TEMP
    TEMP -- "manifest + COMPLETE + verify<br/>atomic publish" --> QUAR
    STATE -- "connection backup API" --> BAK
    QUAR -- "reintroduction copies source" --> UNP
    CANON["Only State/SQLite is canonical message state.<br/>Workspaces and other folders are transport or inspection boundaries."] --- STATE`
}
];
