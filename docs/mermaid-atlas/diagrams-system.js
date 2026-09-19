export const systemDiagrams = [
{
  id: "00",
  title: "Diagram Atlas",
  category: "System",
  code: String.raw`flowchart TD
    ROOT["Conversation Engine Atlas"]
    ROOT --> SYS["System views"]
    SYS --> D01["01 Boundary and authority"]
    SYS --> D02["02 Unprocessed to fully current"]
    SYS --> D03["03 One service cycle"]
    SYS --> D24["24 Stage contract matrix"]
    SYS --> D25["25 Current operational state"]
    ROOT --> STAGES["Stage workflows"]
    STAGES --> D04["04 Intake"]
    STAGES --> D06["06 Mapping"]
    STAGES --> D07["07 Public Safety"]
    STAGES --> D08["08 Original Versions"]
    STAGES --> D09["09 Public Versions"]
    STAGES --> D10["10 Discord Original"]
    STAGES --> D11["11 Discord Public"]
    ROOT --> STATE["State and change"]
    STATE --> D05["05 Planning and durable fanout"]
    STATE --> D13["13 Desired vs live state"]
    STATE --> D17["17 Definitions, contracts, suggestions"]
    STATE --> D20["20 Finished and live meaning"]
    STATE --> D23["23 Task, workspace, result states"]
    ROOT --> REL["Reliability machinery"]
    REL --> D12["12 Model workspace lifecycle"]
    REL --> D14["14 Quarantine and reintroduction"]
    REL --> D15["15 Restart reconciliation"]
    REL --> D18["18 Operational controls"]
    REL --> D19["19 Vault recovery"]
    ROOT --> IMPL["Implementation maps"]
    IMPL --> D16["16 SQLite data model"]
    IMPL --> D21["21 Code ownership"]
    IMPL --> D22["22 Runtime filesystem"]
    NOTE["Read top-down for the human design view.<br/>Follow implementation maps upward for code-level tracing."] --- ROOT`
},
{
  id: "01",
  title: "System Boundary and Authority",
  category: "System",
  code: String.raw`flowchart LR
    F["External Feeders<br/>Create exact JSON envelopes"] --> U["Runtime/Unprocessed<br/>Only normal entrance"]
    subgraph CE["Conversation Engine"]
      U --> S["Single local coordinator<br/>EngineService"]
      S <--> DB[("SQLite vault<br/>Sole live-state authority")]
      S --> TD["Workspaces/STAGE/To_Do<br/>Immutable model requests"]
      DN["Workspaces/STAGE/Done<br/>Returned model work"] --> S
      S --> SG["Runtime/Suggestions/STAGE<br/>Unique JSON review captures"]
      S --> Q["Runtime/Rejections/STAGE<br/>Complete isolated bundles"]
      DB --> V1["current_message_revisions"]
      DB --> V2["current_stage_results"]
    end
    TD --> M["External models<br/>Claude or future workers"]
    M --> DN
    V1 --> C["External consumers<br/>Read only"]
    V2 --> C
    C -. "never writes" .-> DB
    N1["No shared mutable message JSON<br/>No Finalization stage<br/>No retry or defer queue"] --- DB
    N2["Filesystem is transport<br/>SQLite is canonical state"] --- S`
},
{
  id: "02",
  title: "Unprocessed to Fully Current",
  category: "System",
  code: String.raw`flowchart TD
    U["JSON envelope<br/>Runtime/Unprocessed"] --> IV{"Envelope valid?"}
    IV -- "No" --> QE["External quarantine bundle"]
    IV -- "Yes" --> I["Intake transaction<br/>Message + immutable revision + Intake head"]
    I --> P["Plan desired stage tasks"]
    P --> M["Mapping<br/>Model"]
    P --> PS["Public Safety<br/>Model"]
    P --> OV["Original Versions<br/>Model"]
    P --> DO["Discord Original<br/>Deterministic pointer or model ranges"]
    PS --> PV{"Public Versions"}
    OV --> PV
    PS --> DP{"Discord Public"}
    DO --> DP
    M --> MH["Mapping stage head"]
    PS --> PSH["Public Safety stage head"]
    OV --> OVH["Original Versions stage head"]
    DO --> DOH["Discord Original stage head"]
    PV --> PVH["Public Versions stage head"]
    DP --> DPH["Discord Public stage head"]
    MH --> LIVE["Fully current database state<br/>Intake + six transformation heads"]
    PSH --> LIVE
    OVH --> LIVE
    DOH --> LIVE
    PVH --> LIVE
    DPH --> LIVE
    LIVE --> READ["Read-only views for future consumers"]
    M -. "any bounded failure" .-> QM["Total message quarantine"]
    PS -. "any bounded failure" .-> QM
    OV -. "any bounded failure" .-> QM
    DO -. "any bounded failure" .-> QM
    PV -. "any bounded failure" .-> QM
    DP -. "any bounded failure" .-> QM
    NOTE["There is no Finalization gate.<br/>Each valid desired result promotes atomically.<br/>Old heads remain live until replacements promote."] --- LIVE`
},
{
  id: "03",
  title: "One Service Cycle",
  category: "System",
  code: String.raw`flowchart TD
    START["run_once"] --> HALT{"Global halt marker?"}
    HALT -- "Yes" --> REPORT["Return halted report<br/>No mutation"]
    HALT -- "No" --> QR["Resume incomplete quarantine transitions"]
    QR --> REC["Reconcile workspace filesystem with database states"]
    REC --> UNK{"Unknown workspace folders?"}
    UNK -- "Yes" --> QUNK["Externally quarantine each unknown folder"]
    UNK -- "No" --> ABS
    QUNK --> ABS["Claim, validate, and absorb returned work"]
    ABS --> IP{"Intake paused?"}
    IP -- "No" --> SCAN["Scan Unprocessed once<br/>Sort candidates<br/>Take bounded batch"]
    SCAN --> ADMIT["Accept valid envelopes<br/>Quarantine invalid envelopes"]
    IP -- "Yes" --> DIRTY
    ADMIT --> DIRTY["Plan durable dirty messages<br/>Planning always continues"]
    DIRTY --> DP{"Deterministic execution paused?"}
    DP -- "No" --> DET["Execute ready deterministic tasks<br/>Skip paused stages"]
    DP -- "Yes" --> MP
    DET --> MP{"Model dispatch paused?"}
    MP -- "No" --> PUB["Publish ready model workspaces<br/>Skip paused stages"]
    MP -- "Yes" --> END
    PUB --> END["Return cycle counts"]
    END --> LOOP{"Continuous mode?"}
    LOOP -- "Yes and not halted" --> SLEEP["Sleep interval"] --> START
    LOOP -- "No" --> DONE["Stop"]
    CTRL["Persistent controls<br/>intake<br/>deterministic<br/>model_dispatch<br/>stage:name"] -.-> IP
    CTRL -.-> DP
    CTRL -.-> MP
    RULE["Returned work absorbs even when its stage is paused"] --- ABS
    FAIL["Bounded item failure -> quarantine<br/>Database or filesystem failure -> service stop"] --- REC`
},
{
  id: "04",
  title: "Intake in Full",
  category: "Stage",
  code: String.raw`flowchart TD
    U["Runtime/Unprocessed/**/*.json"] --> SCAN["Scan once per cycle<br/>Ignore non-JSON files"]
    SCAN --> LOAD["Parse every candidate once"]
    LOAD --> VALID{"Exact envelope valid?"}
    VALID -- "No" --> EXTQ["External quarantine<br/>Never creates message state"]
    VALID -- "Yes" --> NORM["Normalize timestamp<br/>Validate Archived/... destination<br/>Canonicalize JSON<br/>Hash envelope and message"]
    NORM --> ORDER["Sort valid candidates by<br/>timestamp, destination, position, filename<br/>Take batch limit, default 50"]
    ORDER --> TX["BEGIN one SQLite transaction"]
    TX --> DUP{"Envelope SHA-256 already ingested?"}
    DUP -- "Yes" --> IDEM["Return existing message_id + revision_id<br/>No duplicate records or planning"]
    DUP -- "No" --> SLOT{"chat_id + position exists?"}
    SLOT -- "No" --> NEW["Create message identity<br/>Create immutable revision 1<br/>Status accepted"]
    SLOT -- "Yes" --> REV["Create next immutable revision<br/>Advance message source head<br/>Status revised"]
    NEW --> STORE
    REV --> STORE["Record ingested source identity"]
    STORE --> IR["Create deterministic Intake result<br/>Promote Intake stage head"]
    IR --> FAN["Mark Mapping-neighbor range dirty<br/>position - 2 through position + 4"]
    FAN --> PLAN["Plan initial desired tasks in same transaction<br/>Mapping, Public Safety, Original Versions, Discord Original"]
    PLAN --> AUDIT["Immutable Intake audit event"]
    IDEM --> COMMIT
    AUDIT --> COMMIT["COMMIT"]
    COMMIT --> DELETE["Remove accepted source file"]
    DELETE --> DONE["Message is inside the engine"]
    CRASH["Crash after commit but before removal"] -.-> U
    U -. "same canonical hash" .-> IDEM
    SHAPE["Required envelope<br/>chat: chat_id, destination_rel_path, position<br/>message: speaker, timestamp, text"] --- VALID
    FILES["Code<br/>envelope.py -> scan and validate<br/>intake.py -> batch and transaction<br/>repository.py -> identity and immutable records<br/>planner.py -> desired tasks"] --- TX`
},
{
  id: "05",
  title: "Planning, Dependencies, and Durable Fanout",
  category: "State",
  code: String.raw`flowchart TD
    subgraph TRIG["What marks planning work dirty"]
      SRC["New or revised source"] --> BOUND["Source message + bounded Mapping neighbors"]
      DEF["Definition head changes"] --> ALL1["Every message"]
      CON["Stage contract head changes"] --> ALL2["Every message"]
      PROM["Stage result promotes"] --> SELF["That message"]
    end
    BOUND --> DIRTY[("dirty_messages<br/>Durable and coalescing")]
    ALL1 --> DIRTY
    ALL2 --> DIRTY
    SELF --> DIRTY
    DIRTY --> PLAN["Planner rebuilds from current heads"]
    PLAN --> HASH["Canonical desired SHA-256<br/>revision + contract + payload + exact dependencies"]
    HASH --> SAME{"Matching task already exists?"}
    SAME -- "Yes" --> REUSE["Reuse matching retained task or result"]
    SAME -- "No" --> NEW["Create immutable planned task"]
    REUSE --> TARGET["Advance mutable stage target"]
    NEW --> TARGET
    TARGET --> CLEAR["Clear durable dirty record"]
    subgraph STAGES["Stage dependency graph"]
      R["Current source revision"]
      R --> M["Mapping<br/>source + instructions + Mapping Index<br/>up to 4 previous + 2 following revisions"]
      R --> PS["Public Safety<br/>source + instructions"]
      R --> OV["Original Versions<br/>source + instructions"]
      R --> DO["Discord Original<br/>source + Discord instructions only when long"]
      PS --> PV["Public Versions"]
      OV --> PV
      PS --> DP["Discord Public"]
      DO --> DP
    end
    PLAN --> M
    PLAN --> PS
    PLAN --> OV
    PLAN --> DO
    PLAN --> PV
    PLAN --> DP
    WAIT["Missing required upstream head?<br/>Downstream stage is not planned yet"] --- PV
    WAIT --- DP
    RULE["Every task captures its exact contract ID and hashes.<br/>Later validation never substitutes newer heads."] --- HASH`
},
{
  id: "24",
  title: "Stage Contract Matrix",
  category: "System",
  code: String.raw`flowchart TD
    I["Intake<br/>Worker: deterministic, internal<br/>Input: exact envelope<br/>Output: source_revision pointer<br/>Workspace: none"]
    M["Mapping<br/>Worker: model<br/>Input: original + context + instructions + index<br/>Return: response.json<br/>Output: exact Mapping title list<br/>Validator: mapping v1"]
    PS["Public Safety<br/>Worker: model<br/>Input: original + instructions<br/>Return: response.json<br/>Output: decision + redactions + safe text<br/>Validator: public_safety v1"]
    OV["Original Versions<br/>Worker: model<br/>Input: original + instructions<br/>Return: version_1..9.txt<br/>Output: nine version lanes<br/>Validator: versions v1"]
    PV["Public Versions<br/>Worker: dynamic<br/>Public -> deterministic result pointer<br/>Private -> model on safe text<br/>Output: pointer or nine lanes<br/>Validator: versions v1 when model"]
    DO["Discord Original<br/>Worker: dynamic<br/>Short -> deterministic source pointer<br/>Long -> model exact ranges<br/>Output: pointer or chunks<br/>Validator: discord_ranges v1 when model"]
    DP["Discord Public<br/>Worker: dynamic<br/>Public -> Discord Original result pointer<br/>Private short -> Safety source pointer<br/>Private long -> model exact ranges<br/>Output: pointer or chunks<br/>Validator: discord_ranges v1 when model"]
    I --> M
    I --> PS
    I --> OV
    I --> DO
    PS --> PV
    OV --> PV
    PS --> DP
    DO --> DP
    CONTRACT["Every stage task captures<br/>contract_id + contract SHA-256<br/>worker policy<br/>workspace response specification<br/>output JSON schema<br/>validator key/version<br/>deterministic config"] --- M
    CONTRACT --- PS
    CONTRACT --- OV
    CONTRACT --- PV
    CONTRACT --- DO
    CONTRACT --- DP`
},
{
  id: "25",
  title: "Current Operational State",
  category: "System",
  code: String.raw`flowchart TD
    ENGINE["Conversation Engine implementation<br/>Local Python + SQLite subsystem"] --> VAULT["Real vault initialized<br/>Identity protected<br/>Not globally halted"]
    VAULT --> EMPTY["Current live contents<br/>0 messages<br/>0 dirty messages<br/>0 tasks<br/>0 workspaces<br/>0 stage heads<br/>0 quarantines<br/>0 Unprocessed envelopes"]
    VAULT --> SEEDED["Bootstrap installed<br/>5 instruction definitions<br/>Placeholder Mapping Index<br/>6 stage contracts + validators"]
    SEEDED --> BLOCK["Mapping Index is deliberately non-live<br/>and contains no production titles"]
    BLOCK --> PAUSE["Intake persistently paused<br/>Fresh vault approval reason recorded"]
    PAUSE --> BEFORE["Required before live admission"]
    BEFORE --> MAP["Publish approved live Mapping Index"]
    MAP --> REVIEW["Review current instructions and contracts"]
    REVIEW --> UNPAUSE["Explicitly unpause Intake with reason"]
    UNPAUSE --> RUN["Run local coordinator continuously"]
    OUT["Intentionally not connected yet<br/>No legacy migration<br/>No old automations<br/>No feeders<br/>No models<br/>No consumers"] --- EMPTY
    TEST["Implemented behavior is covered by<br/>44 disposable-vault acceptance tests"] --- ENGINE
    RULE["Nothing from the old Message Pipeline<br/>is an implementation dependency."] --- OUT`
}
];
