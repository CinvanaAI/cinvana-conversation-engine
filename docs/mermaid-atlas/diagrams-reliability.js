export const reliabilityDiagrams = [
{
  id: "12",
  title: "Model Workspace Lifecycle",
  category: "Reliability",
  code: String.raw`flowchart TD
    READY["Ready desired model task<br/>status planned + still stage target"] --> ACTIVE{"Live workspace already exists<br/>for same message and stage?"}
    ACTIVE -- "Yes" --> WAIT["Wait<br/>Never replace or collide with live model work"]
    ACTIVE -- "No" --> DBPREP["Create globally unique workspace ID<br/>Database state: preparing"]
    DBPREP --> DRAFT["Create Workspaces/Drafts/WORKSPACE_ID"]
    DRAFT --> INPUTS["Materialize exact captured inputs<br/>message.txt<br/>instructions.md when required<br/>Mapping extras when required"]
    INPUTS --> MAN["Hash every input into immutable manifest"]
    MAN --> REQ["Write request.json<br/>Workspace + task IDs<br/>Captured contract and validator<br/>Input manifest + output contract"]
    REQ --> SAVE["Store request and manifest hashes in SQLite"]
    SAVE --> PUB["Atomic rename Drafts -> To_Do"]
    PUB --> DBPUB["Database workspace/task state: published"]
    DBPUB --> MODEL["External model works only in folder"]
    MODEL --> OUT["Add response.json or contiguous version files<br/>Optional plain-text Suggestions"]
    OUT --> MOVE["Model moves whole folder To_Do -> Done"]
    MOVE --> CLAIM["Service atomically moves Done -> Claimed"]
    CLAIM --> RET["Database workspace/task state: returned"]
    RET --> VERIFY["Verify request.json unchanged<br/>Verify every captured input hash<br/>Reject unexpected entries<br/>Require contracted outputs"]
    VERIFY --> SEM["Validate with captured validator version<br/>Captured definitions, source, dependencies,<br/>response schema and output schema"]
    SEM --> OK{"Valid?"}
    OK -- "No" --> Q["Total message quarantine"]
    OK -- "Yes" --> SUG["Atomically capture optional suggestion<br/>Suggestions/STAGE/MESSAGE_ID_WORKSPACE_ID.json"]
    SUG --> PROM["Create immutable result<br/>Promote only if task is still desired<br/>Otherwise retain only while referenced"]
    PROM --> ABS["Database workspace state: absorbed"]
    ABS --> CLEAN["Delete Claimed folder"]
    RULE["Folder names are unique and never reused.<br/>Only one preparing, published, or returned workspace<br/>may exist per message + stage."] --- ACTIVE
    CODE["Code path<br/>repository.py workspace states<br/>workspaces.py materialize, reconcile, absorb<br/>validators.py captured validation"] --- DBPREP`
},
{
  id: "13",
  title: "Desired State, Live State, and Coalescing",
  category: "State",
  code: String.raw`sequenceDiagram
    participant Live as Promoted stage head
    participant Pub as Definition or source publisher
    participant Dirty as Durable dirty_messages
    participant Plan as Planner
    participant DB as Desired stage target
    participant WS as Existing model workspace
    participant Model as External model
    Note over Live: Result A is current and readable
    Note over WS: Workspace B is already published<br/>with its own captured contract and inputs
    Pub->>Dirty: Publish newer change C and mark message dirty
    Dirty->>Plan: Rebuild from current heads
    Plan->>DB: Target becomes Task C
    Note over Live: Result A remains live
    Note over WS: Workspace B is never overwritten
    Note over DB: Task B remains while its workspace is active<br/>but is no longer desired
    Pub->>Dirty: Publish even newer change D
    Dirty->>Plan: Rebuild again
    Plan->>DB: Target becomes Task D
    Note over DB: Task C is skipped before publication
    Note over WS: One live workspace blocks C and D publication
    Model->>WS: Return Workspace B
    WS->>DB: Validate using B's captured contract
    DB-->>Live: Do not advance head
    Note over DB: Valid Result B is temporarily superseded
    Plan->>DB: Select latest desired Task D
    DB->>WS: Publish fresh unique Workspace D
    Model->>WS: Return valid D result
    WS->>DB: Validate captured D contract
    DB->>Live: Atomically replace Result A with Result D
    DB->>DB: Reclaim unreachable Results A and B and obsolete tasks
    Note over Live,DB: What is stays live until its valid desired replacement exists
    Note over Dirty,Plan: Repeated updates coalesce toward latest desired state<br/>No live workspace is replaced or reused`
},
{
  id: "14",
  title: "Quarantine, Halt Fuse, and Reintroduction",
  category: "Reliability",
  code: String.raw`flowchart TD
    FAIL{"Bounded item failure"}
    FAIL -- "Invalid envelope or unknown folder" --> EXT["External identity<br/>No live message state required"]
    FAIL -- "Known message/workspace failure" --> MSG["Capture complete message database snapshot"]
    EXT --> RECORD["Create resumable quarantine record<br/>state: preparing"]
    MSG --> RECORD
    RECORD --> TEMP["Build resumable Runtime/Temp/QUARANTINE_ID"]
    TEMP --> BASE["Write reason.json<br/>database_snapshot.json<br/>source_envelope.json when available"]
    BASE --> MOVE["Move every live workspace folder<br/>Drafts, To_Do, Done, Claimed<br/>Move every captured message suggestion"]
    EXT --> MOVEEXT["Move external source or folder"]
    MOVEEXT --> MAN
    MOVE --> MAN["Create SHA-256 tree manifest"]
    MAN --> COMPLETE["Write COMPLETE marker"]
    COMPLETE --> ATOMIC["Atomic publish Temp -> Rejections/STAGE/QUARANTINE_ID"]
    ATOMIC --> VERIFY{"Bundle verifies exactly?"}
    VERIFY -- "No" --> STOP["System failure<br/>Stop and repair database/filesystem"]
    VERIFY -- "Yes" --> PUBLISHED["Record state: published"]
    PUBLISHED --> DELETE["Delete live message and operational rows<br/>Delete message audit history"]
    DELETE --> SANITIZE["Record state: completed<br/>Remove message ID, source path,<br/>private reason and snapshot from SQLite"]
    SANITIZE --> COUNT{"Completed quarantines<br/>in rolling 60 minutes"}
    COUNT -- "1 through 5" --> ISOLATED["System continues"]
    COUNT -- "6 or more" --> HALT["Write persistent global halt marker<br/>Next cycle performs no mutation"]
    ISOLATED --> HUMAN["Human inspects bug and repairs system"]
    HALT --> HUMAN
    HUMAN --> REINTRO["quarantine-reintroduce"]
    REINTRO --> V2{"Completed bundle still verifies?"}
    V2 -- "No" --> STOP
    V2 -- "Yes" --> COPY["Copy source envelope to normal Unprocessed<br/>Write REINTRODUCED.json + audit event"]
    COPY --> INTAKE["Ordinary Intake, planning, validation,<br/>promotion or quarantine decides outcome"]
    RULE["Quarantine is complete isolation and diagnosis.<br/>It is never a retry or defer queue."] --- HUMAN`
},
{
  id: "15",
  title: "Restart Reconciliation",
  category: "Reliability",
  code: String.raw`flowchart TD
    RESTART["Service starts or begins next cycle"] --> LOCK["Acquire Runtime/State/service.lock<br/>Second coordinator fails immediately"]
    LOCK --> HALT{"Global halt marker?"}
    HALT -- "Yes" --> STOP["Stop without mutation"]
    HALT -- "No" --> QP["Resume quarantine records<br/>preparing -> published -> completed"]
    QP --> WS["Reconcile every known workspace"]
    WS --> PREP{"Database state: preparing"}
    PREP -- "No request captured" --> DISC["Delete partial Draft/To_Do<br/>Discard workspace record<br/>Return desired task to planned"]
    PREP -- "Request captured + Draft exists" --> VPREP["Verify inputs, then atomic Draft -> To_Do<br/>Mark published"]
    PREP -- "Request captured + To_Do exists" --> VPREP
    PREP -- "No folder exists" --> DISC
    WS --> PUB{"Database state: published"}
    PUB -- "To_Do exists" --> VPUB["Verify published inputs remain unchanged"]
    PUB -- "Done exists" --> CLAIM["Atomic Done -> Claimed<br/>Mark returned"]
    PUB -- "Claimed exists" --> RET["Finish database claim<br/>Mark returned"]
    PUB -- "No expected folder" --> Q["Total message quarantine"]
    WS --> RETURNED{"Database state: returned"}
    RETURNED -- "Done exists and Claimed absent" --> CLAIM
    RETURNED -- "Claimed exists" --> ABSORB["Normal validation and absorption"]
    RETURNED -- "Claimed missing" --> Q
    WS --> ABSORBED{"Database state: absorbed"}
    ABSORBED --> CLEAN["Remove any leftover Draft, To_Do,<br/>Done, or Claimed folder"]
    WS --> UNKNOWN{"Folder exists without database identity?"}
    UNKNOWN -- "Yes" --> EXTQ["External quarantine"]
    UNKNOWN -- "No" --> CONT["Continue service cycle"]
    CRASH1["Accepted source file survived post-commit crash"] --> IDEM["Next Intake sees canonical envelope hash<br/>Treats it as exact duplicate and removes it"]
    CRASH2["dirty_messages survived process exit"] --> REPLAN["Planner resumes durable desired-state work"]
    CRASH3["Promoted folder survived cleanup crash"] --> CLEAN
    RULE["Reconciliation completes infrastructure transitions.<br/>It never retries failed model semantics."] --- CONT`
},
{
  id: "17",
  title: "Definitions, Instructions, Contracts, and Suggestions",
  category: "State",
  code: String.raw`flowchart TD
    INIT["Fresh vault initialization"] --> SEED["Load packaged bootstrap.json"]
    SEED --> VALID["Verify definition shapes<br/>Verify every validator key/version exists"]
    VALID --> DEFS["Publish immutable definition versions<br/>Mapping instructions<br/>Public Safety instructions<br/>Original/Public Versions instructions<br/>Discord instructions<br/>Mapping Index"]
    VALID --> CONS["Publish six immutable stage contracts"]
    DEFS --> HEADS["Set mutable definition heads"]
    CONS --> CHEADS["Set mutable contract heads"]
    HEADS --> PAUSE["Intake starts paused"]
    CHEADS --> PAUSE
    PAUSE --> MAP["Publish real live Mapping Index"]
    MAP --> APPROVE["Explicitly unpause Intake with reason"]
    SUG["Runtime/Suggestions/STAGE/<br/>MESSAGE_ID_WORKSPACE_ID.json<br/>Human-review proposal only"] --> REVIEW{"Human approves a change?"}
    REVIEW -- "No" --> KEEP["Leave current definitions unchanged"]
    REVIEW -- "Yes" --> PUB["Publish definition or contract JSON through CLI"]
    DIRECT["Directly authored operational update"] --> PUB
    PUB --> CHECK["Validate payload<br/>Canonicalize and SHA-256"]
    CHECK --> SAME{"Same content as current head?"}
    SAME -- "Yes" --> IDEM["Return unchanged<br/>No new version"]
    SAME -- "No" --> IMM["Create or reuse immutable version"]
    IMM --> ADV["Advance only the mutable head"]
    ADV --> FAN["Mark every message dirty in same transaction"]
    FAN --> PLAN["Planner creates replacement desired tasks"]
    PLAN --> LIVE["Old promoted results remain live<br/>until replacements validate and promote"]
    OLD["Already published workspace"] --> CAP["Keeps its captured definition,<br/>contract, schema, and validator version"]
    CAP --> VAL["Can still validate after heads change"]
    VAL --> DISP{"Still desired?"}
    DISP -- "No" --> SUPER["Retained only while referenced<br/>Then reclaimed"]
    DISP -- "Yes" --> PROM["Promoted result"]
    RULE["Suggestions never mutate the vault automatically.<br/>Validator implementations needed by live old workspaces<br/>must remain available append-only."] --- REVIEW`
},
{
  id: "18",
  title: "Operational Controls",
  category: "Operations",
  code: String.raw`flowchart TD
    OP["Operator command with required reason"] --> TYPE{"Control type"}
    TYPE -- "pause or unpause intake" --> INTAKE["Persistent controls.intake"]
    TYPE -- "pause or unpause deterministic" --> DET["Persistent controls.deterministic"]
    TYPE -- "pause or unpause model_dispatch" --> MODEL["Persistent controls.model_dispatch"]
    TYPE -- "pause or unpause stage:name" --> STAGE["Persistent per-stage control"]
    TYPE -- "drain" --> DRAIN["Audited Intake pause<br/>Accepted work continues draining"]
    TYPE -- "halt" --> HALT["External Runtime/State/HALTED.json<br/>Immediate service-wide stop"]
    TYPE -- "resume" --> RESUME["Audit reason and remove halt marker"]
    INTAKE --> ICHECK{"Unpausing Intake?"}
    ICHECK -- "Yes" --> MAP{"Current Mapping Index live_ready<br/>and contains titles?"}
    MAP -- "No" --> REFUSE["Refuse unpause"]
    MAP -- "Yes" --> APPLY["Apply control and audit"]
    ICHECK -- "No" --> APPLY
    DET --> APPLY
    MODEL --> APPLY
    STAGE --> APPLY
    DRAIN --> APPLY
    subgraph EFFECTS["What continues while paused"]
      PLAN["Durable dirty planning always continues"]
      ABS["Already returned model work always validates and absorbs"]
      LIVE["Existing promoted state stays readable"]
    end
    INTAKE -. "blocks new admission only" .-> EFFECTS
    DET -. "blocks deterministic execution only" .-> EFFECTS
    MODEL -. "blocks new workspace publication only" .-> EFFECTS
    STAGE -. "blocks new execution/publication for that stage" .-> EFFECTS
    HALT --> ZERO["run_once returns halted immediately<br/>No reconciliation, absorption, Intake,<br/>planning, execution, or publication"]
    RESUME --> NEXT["Next service cycle may operate normally"]
    TARGETS["Stage controls<br/>mapping<br/>public_safety<br/>original_versions<br/>public_versions<br/>discord_original<br/>discord_public"] --- STAGE`
},
{
  id: "20",
  title: "What Finished and Live State Mean",
  category: "State",
  code: String.raw`flowchart LR
    subgraph HIST["Durable records"]
      SR["Source revisions<br/>Every accepted revision preserved"]
      T["Tasks<br/>Desired or still referenced"]
      R["Stage results<br/>Live or still referenced"]
      D["Definitions and contracts<br/>Every published version"]
      A["Audit events"]
    end
    subgraph HEADS["Mutable selectors"]
      MH["message_heads<br/>Current source revision"]
      DT["stage_targets<br/>Newest desired task per stage"]
      SH["stage_heads<br/>Currently promoted result per stage"]
      DH["definition_heads"]
      CH["contract_heads"]
    end
    SR --> MH
    T --> DT
    R --> SH
    D --> DH
    D --> CH
    MH --> V1["current_message_revisions view"]
    SH --> V2["current_stage_results view"]
    V1 --> READ["Read-only consumer query"]
    V2 --> READ
    READ --> NEED{"What does this consumer need?"}
    NEED --> O["Original message or current source"]
    NEED --> M["Mapping titles"]
    NEED --> P["Public-safe text and redactions"]
    NEED --> VER["Original or public version lanes"]
    NEED --> DISC["Discord pointers or exact chunks"]
    REPLACE["Replacement work pending"] -.-> DT
    REPLACE -. "does not withdraw" .-> SH
    NO["No completed folder<br/>No compiled shared message JSON<br/>No cold archive database<br/>No consumer queue or delivery state"] --- READ
    FIN["Finished means the desired stage results have promoted.<br/>Live means whatever stage_heads currently select."] --- SH`
},
{
  id: "23",
  title: "Task, Workspace, and Result State Machines",
  category: "State",
  code: String.raw`flowchart LR
    subgraph TASK["Task lifecycle"]
      TP["planned"] -->|model selected| TPREP["preparing"]
      TPREP --> TPUB["published"]
      TPUB --> TRET["returned"]
      TRET --> TDES{"Still desired at promotion?"}
      TDES -- "Yes" --> TPRO["promoted"]
      TDES -- "No" --> TSUP["superseded"]
      TP -->|deterministic execution| TDES
      TPREP -->|incomplete unpublished draft discarded| TP
      TOLD["validated<br/>Allowed by schema but no current transition uses it"]:::reserved
    end
    subgraph WORK["Workspace lifecycle"]
      WP["preparing"] --> WPUB["published"]
      WPUB --> WRET["returned"]
      WRET --> WABS["absorbed"]
      WP -->|discard before publication| WDEL["record deleted"]
      WABS --> WCLEAN["all folder copies removed"]
    end
    subgraph RESULT["Result disposition"]
      CREATE["Create immutable result + dependency copies"] --> CHECK{"task_id equals current stage target?"}
      CHECK -- "Yes" --> HEAD["Advance stage_heads<br/>Result is live"]
      CHECK -- "No" --> HISTORY["Retain only while referenced<br/>Do not change live head"]
      HEAD --> DIRTY["Mark message dirty for downstream planning"]
      HISTORY --> DIRTY
      DIRTY --> SWEEP["Reference-aware retention sweep"]
      SWEEP --> DELETE["Delete unreachable results and obsolete tasks"]
    end
    TPREP -. "owns" .-> WP
    TRET -. "absorbed from" .-> WRET
    TDES --> CREATE
    WRET --> CREATE
    CREATE --> WABS
    classDef reserved fill:#eeeeee,stroke:#888,color:#555
    RULE["Only planned tasks that are still stage targets dispatch.<br/>A live workspace for the same message + stage blocks another."] --- TP`
}
];
