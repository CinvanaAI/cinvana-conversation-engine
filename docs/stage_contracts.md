# Stage Contracts

Every task captures an immutable contract version containing:

- stage name and worker policy;
- workspace response specification;
- normalized output schema;
- semantic validator key and version;
- deterministic configuration;
- exact contract SHA-256.

Validator implementations are append-only by key/version. A newer contract may select a newer validator, but an older published workspace remains validatable with its captured implementation.

## Intake

Source: exact validated envelope.

Result: immutable source revision pointer containing envelope hash, message hash, and character count.

Intake is deterministic and has no model workspace.

## Mapping

Source: original message, up to four previous and two following current source revisions, Mapping instructions, and the exact Mapping Index definition.

Worker: model.

Response:

```json
{"discord_public_indexing":["Exact Mapping Title"]}
```

Every title must exactly match the captured index. Duplicates and invented titles fail. An empty list is valid.

## Public Safety

Source: original message and exact Public Safety instructions.

Worker: model.

Response:

```json
{
  "decision":"Private",
  "redactions":[
    {"start":0,"end":4,"replacement":"(name)","category":"identity"}
  ]
}
```

Public requires no redactions. Private requires at least one effective, bounded, non-overlapping redaction. The immutable result stores only the decision, exact source reference, and redaction operations. The shared resolver applies ranges when a downstream workspace or read-only consumer requests public text.

## Original Versions

Source: original message and exact Original Versions instructions.

Worker: model.

Response: zero or more contiguous version_1.txt through version_9.txt files.

Each authored file must be nonempty and strictly shorter than the preceding source. The first missing level is the semantic floor. Authored text is stored once. Every remaining level stores a pointer to the semantic floor rather than another copy of its text.

## Public Versions

Source depends on Public Safety.

When Public Safety is Public, the result is a deterministic pointer to the current Original Versions result.

When Private, the resolver applies Public Safety operations to create the temporary `message.txt` used with the exact Public Versions instructions. The derived public-safe source is not persisted. Public authored versions use the same compact sequential version contract, and redactions and placeholders are treated as source truth.

## Discord Original

Source: original message and the captured max_chars contract.

For a short message, the result is a deterministic source-revision pointer.

For a long message, a model returns exact contiguous ranges:

```json
{
  "chunks":[
    {"index":1,"start":0,"end":2000},
    {"index":2,"start":2000,"end":2100}
  ]
}
```

The engine slices text transiently to prove exact reconstruction. The immutable result stores only the exact source reference, character limit, and ordered ranges. Models never return rewritten chunk text, and chunk text is not persisted.

## Discord Public

Source depends on Public Safety.

The three valid outcomes are:

- Public source: deterministic result pointer to Discord Original.
- Private public-safe text within max_chars: deterministic pointer to the Public Safety result.
- Private public-safe text over max_chars: model-authored exact ranges over the public-safe text.

All references use immutable database IDs. Pointer files do not exist.

## Dependency Rules

Every task depends on its exact contract version.

Additional dependencies:

- Mapping: source revision, Mapping instructions, Mapping Index, every context revision.
- Public Safety: source revision and instructions.
- Original Versions: source revision and instructions.
- Public Versions model: Public Safety result and instructions.
- Public Versions pointer: Public Safety and Original Versions results.
- Discord Original short: source revision.
- Discord Original long: source revision and Discord instructions.
- Discord Public pointer/ranges: Public Safety result, source/result pointer, and Discord instructions only when model reasoning is required.
