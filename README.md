# Conversation Engine

Keep a conversation source and its evolving transformations consistent, traceable, and recoverable in a local vault.

## Try it

Python 3.12+. Run from this checkout:

```sh
python -m pip install -e .
python -m examples.offline_demo
```

**Input:** A source message, an explicitly configured Workshop mapping, and three synthetic worker replies.

**Result:** All six stages acquire current results; level 9 resolves to “Review the fixture.” The demo creates and removes its own disposable vault.

See [the captured example](examples/RESULT.md) for the observed output and reproduction command.

## How it works

Each workspace captures the contract and definitions it must satisfy. The validator promotes results only when that captured work remains relevant. Current outputs can reference source or other results instead of copying text.

Source: [conversation_engine/service.py](conversation_engine/service.py), [conversation_engine/cli.py](conversation_engine/cli.py), [tests/test_workspaces_and_contracts.py](tests/test_workspaces_and_contracts.py).

## Use it for your work

For your own vault, run `conversation-engine --root YOUR_VAULT init`, inspect `verify` and `status`, then follow [the contracts](conversation_engine/seed) and [workspace code](conversation_engine/workspaces.py) before connecting a worker. Source data and runtime stay local.

## Scope

The engine does not supply a semantic model. The demo supplies labeled fixture replies; real operation needs a feeder, an approved mapping definition and a worker that returns the documented files. New vault intake begins paused. Use a short runtime root on Windows.

Owned code is available under the [MIT license](LICENSE.md).
