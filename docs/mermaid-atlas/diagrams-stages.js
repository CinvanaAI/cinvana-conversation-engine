export const stageDiagrams = [
{
  id: "06",
  title: "Mapping Stage",
  category: "Stage",
  code: String.raw`flowchart TD
    START["Desired Mapping task"] --> CAP["Capture exact dependencies<br/>Current source revision<br/>Mapping instructions<br/>Mapping Index<br/>Context revision IDs and hashes"]
    CAP --> CTX["Build context<br/>Up to 4 previous current messages<br/>Up to 2 following current messages"]
    CTX --> WS["Publish unique workspace"]
    subgraph FILES["Mapping workspace"]
      F1["message.txt<br/>Original target message"]
      F2["instructions.md<br/>Captured Mapping instructions"]
      F3["mapping_context.md<br/>Neighbor messages + marked target"]
      F4["mapping_reference.json<br/>Allowed titles"]
      F5["request.json<br/>Task, contract, manifests, hashes"]
    end
    WS --> FILES
    FILES --> MODEL["Model reasoning"]
    MODEL --> RESP["response.json<br/>{ discord_public_indexing: [exact titles] }"]
    RESP --> SCHEMA{"Exact response schema?"}
    SCHEMA -- "No" --> Q["Total message quarantine"]
    SCHEMA -- "Yes" --> TITLES{"Every selected title exists<br/>in captured Mapping Index?<br/>No duplicates?"}
    TITLES -- "No" --> Q
    TITLES -- "Yes" --> OUT["Immutable Mapping result<br/>Selected title list<br/>Empty list is valid"]
    OUT --> DES{"Task still desired?"}
    DES -- "Yes" --> HEAD["Promote Mapping stage head"]
    DES -- "No" --> HIST["Retain only while referenced<br/>Then reclaim"]
    CHANGE["A Mapping Index, instruction, source,<br/>context revision, or contract change"] -.-> DIRTY["Marks affected planning work dirty"]
    DIRTY -.-> START
    CODE["Code path<br/>planner.py:mapping<br/>workspaces.py:_mapping_context<br/>validators.py:mapping_v1"] --- CAP`
},
{
  id: "07",
  title: "Public Safety Stage",
  category: "Stage",
  code: String.raw`flowchart TD
    START["Desired Public Safety task"] --> CAP["Capture source revision<br/>Captured Public Safety instructions<br/>Captured stage contract"]
    CAP --> WS["Unique model workspace"]
    subgraph FILES["Public Safety workspace"]
      M["message.txt<br/>Original message"]
      I["instructions.md<br/>Captured safety instructions"]
      R["request.json<br/>Exact response contract + hashes"]
    end
    WS --> FILES
    FILES --> MODEL["Model classifies and identifies exact ranges"]
    MODEL --> RESP["response.json<br/>decision: Public or Private<br/>redactions: start, end, replacement, category"]
    RESP --> DEC{"Decision"}
    DEC -- "Public" --> PUBCHK{"Redactions empty?"}
    PUBCHK -- "No" --> Q["Total message quarantine"]
    PUBCHK -- "Yes" --> KEEP["Store empty redaction recipe"]
    DEC -- "Private" --> PRCHK{"At least one effective redaction?<br/>All ranges bounded and non-overlapping?<br/>Every replacement changes its span?"}
    PRCHK -- "No" --> Q
    PRCHK -- "Yes" --> APPLY["Engine proves ranges by applying them<br/>without persisting derived text"]
    KEEP --> OUT["Immutable result<br/>source pointer + decision<br/>redaction operations only"]
    APPLY --> OUT
    OUT --> HEAD["Promote if still desired"]
    HEAD --> FAN["Mark message dirty"]
    FAN --> PV["Enable Public Versions planning"]
    FAN --> DP["Enable Discord Public planning"]
    RULE["The shared resolver creates public text<br/>only for workspaces and read-only consumers."] --- APPLY
    CODE["Code path<br/>planner.py:public_safety<br/>validators.py:public_safety_v2"] --- CAP`
},
{
  id: "08",
  title: "Original Versions Stage",
  category: "Stage",
  code: String.raw`flowchart TD
    START["Desired Original Versions task"] --> CAP["Capture original source revision<br/>Original Versions instructions<br/>Nine-lane contract"]
    CAP --> WS["Unique model workspace"]
    subgraph FILES["Original Versions workspace"]
      M["message.txt<br/>Original message"]
      I["instructions.md<br/>Captured versioning instructions"]
      R["request.json<br/>Sequential response specification"]
    end
    WS --> FILES
    FILES --> MODEL["Model compresses until semantic floor"]
    MODEL --> RETURN["Returns zero or more files<br/>version_1.txt through version_9.txt"]
    RETURN --> ORDER{"Files contiguous from version_1?<br/>No file after first missing level?"}
    ORDER -- "No" --> Q["Total message quarantine"]
    ORDER -- "Yes" --> CHECK{"Every authored file nonempty<br/>and strictly shorter than<br/>its preceding source?"}
    CHECK -- "No" --> Q
    CHECK -- "Yes" --> FLOOR["First missing level becomes semantic floor<br/>Zero authored files is valid"]
    FLOOR --> FILL["Engine creates nine addressable levels<br/>Authored levels store returned text<br/>Remaining levels point to semantic floor"]
    FILL --> OUT["Immutable versions result<br/>Source pointer<br/>Authored text once<br/>Compact level pointers"]
    OUT --> HEAD["Promote Original Versions head<br/>if task remains desired"]
    HEAD --> DOWN["May unlock Public Versions<br/>when Public Safety is Public"]
    RULE["All nine levels resolve without storing<br/>fill-forward text more than once."] --- FILL
    CODE["Code path<br/>planner.py:original_versions<br/>validators.py:versions_v2"] --- CAP`
},
{
  id: "09",
  title: "Public Versions Stage",
  category: "Stage",
  code: String.raw`flowchart TD
    WAIT["Wait for promoted Public Safety result"] --> DEC{"Safety decision"}
    DEC -- "Public" --> OV{"Original Versions head available?"}
    OV -- "No" --> HOLD["Do not plan yet"]
    OV -- "Yes" --> PTR["Create deterministic task<br/>Capture Safety + Original Versions result IDs"]
    PTR --> POUT["Result pointer to Original Versions<br/>No workspace and no duplicated versions"]
    DEC -- "Private" --> CAP["Capture Public Safety recipe<br/>Public Versions instructions"]
    CAP --> WS["Publish model workspace"]
    subgraph FILES["Private Public Versions workspace"]
      M["message.txt<br/>Already redacted public-safe text"]
      I["instructions.md<br/>Preserve every redaction and placeholder"]
      R["request.json<br/>Nine sequential version lanes"]
    end
    WS --> FILES
    FILES --> MODEL["Model compresses public-safe text"]
    MODEL --> VAL["Same sequential validator<br/>Contiguous files, nonempty,<br/>strictly shorter, pointer fill"]
    VAL --> MOUT["Immutable compact versions result<br/>Source is captured Safety result"]
    POUT --> DES{"Still desired?"}
    MOUT --> DES
    DES -- "Yes" --> HEAD["Promote Public Versions head"]
    DES -- "No" --> HIST["Retain only while referenced<br/>Then reclaim"]
    Q["Invalid private return -> total message quarantine"] -.-> VAL
    RULE["Public messages reuse Original Versions exactly.<br/>Private messages are compressed only after redaction."] --- DEC
    CODE["Code path<br/>planner.py:public_versions<br/>executor.py for pointer<br/>validators.py:versions_v2 for model"] --- WAIT`
},
{
  id: "10",
  title: "Discord Original Stage",
  category: "Stage",
  code: String.raw`flowchart TD
    START["Current original source revision"] --> LIMIT["Read captured max_chars<br/>Bootstrap value: 2000"]
    LIMIT --> SIZE{"Original character count<br/>within max_chars?"}
    SIZE -- "Yes" --> DET["Deterministic source_pointer task"]
    DET --> POUT["Immutable output<br/>Pointer to exact source revision<br/>No workspace"]
    SIZE -- "No" --> CAP["Capture source revision<br/>Discord instructions<br/>Exact max_chars contract"]
    CAP --> WS["Publish model workspace"]
    subgraph FILES["Long Discord Original workspace"]
      M["message.txt<br/>Exact original text"]
      I["instructions.md<br/>Return ranges, never rewritten chunks"]
      R["request.json<br/>Response schema + max_chars"]
    end
    WS --> FILES
    FILES --> RESP["response.json<br/>chunks: index, start, end"]
    RESP --> IDX{"Indexes contiguous from 1?"}
    IDX -- "No" --> Q["Total message quarantine"]
    IDX -- "Yes" --> RANGE{"Ranges start at 0,<br/>contiguous, nonempty, in bounds,<br/>each <= max_chars?"}
    RANGE -- "No" --> Q
    RANGE -- "Yes" --> SLICE["Engine slices source text itself"]
    SLICE --> RECON{"Concatenated chunks exactly<br/>reconstruct original?"}
    RECON -- "No" --> Q
    RECON -- "Yes" --> ROUT["Immutable ranges output<br/>Source pointer + max_chars<br/>Offsets only"]
    POUT --> HEAD["Promote Discord Original head if desired"]
    ROUT --> HEAD
    HEAD --> DOWN["May unlock Discord Public for Public messages"]
    RULE["Model chooses boundaries only.<br/>It cannot rewrite Discord content."] --- SLICE
    CODE["Code path<br/>planner.py:discord_original<br/>executor.py for short pointer<br/>validators.py:discord_ranges_v2 for long text"] --- START`
},
{
  id: "11",
  title: "Discord Public Stage",
  category: "Stage",
  code: String.raw`flowchart TD
    WAIT["Wait for promoted Public Safety result"] --> DEC{"Safety decision"}
    DEC -- "Public" --> DO{"Discord Original head available?"}
    DO -- "No" --> HOLD["Do not plan yet"]
    DO -- "Yes" --> RP["Deterministic result_pointer<br/>Reuse Discord Original result exactly"]
    DEC -- "Private" --> TEXT["Resolve public-safe text transiently<br/>from source + redaction recipe"]
    TEXT --> SIZE{"Public-safe character count<br/>within captured max_chars?"}
    SIZE -- "Yes" --> SP["Deterministic source_pointer<br/>Point to Public Safety result"]
    SIZE -- "No" --> CAP["Capture Public Safety result<br/>Discord instructions + max_chars"]
    CAP --> WS["Publish model range workspace"]
    subgraph FILES["Long private Discord Public workspace"]
      M["message.txt<br/>Exact public-safe text"]
      I["instructions.md<br/>Return offsets only"]
      R["request.json<br/>Captured range contract"]
    end
    WS --> FILES
    FILES --> RESP["response.json<br/>Exact contiguous ranges"]
    RESP --> VAL{"Indexes contiguous?<br/>No gaps or overlaps?<br/>Each chunk within limit?<br/>Exact reconstruction?"}
    VAL -- "No" --> Q["Total message quarantine"]
    VAL -- "Yes" --> RANGE["Engine slices public-safe text<br/>Immutable ranges result"]
    RP --> DES{"Task remains desired?"}
    SP --> DES
    RANGE --> DES
    DES -- "Yes" --> HEAD["Promote Discord Public head"]
    DES -- "No" --> HIST["Retain only while referenced<br/>Then reclaim"]
    THREE["Exactly three valid outcomes<br/>Public -> Discord Original pointer<br/>Private short -> Public Safety pointer<br/>Private long -> exact ranges"] --- DEC
    CODE["Code path<br/>planner.py:discord_public<br/>executor.py for pointers<br/>validators.py:discord_ranges_v2 for ranges"] --- WAIT`
}
];
