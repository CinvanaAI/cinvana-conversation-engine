# Recorded first use

This output was produced by the included example with network connections disabled. Synthetic provider or worker replies are identified by the example; no real model quality or billing is implied.

From the installed checkout:

```sh
python -m examples.offline_demo
```

[Complete recorded output](result.json)

```text
{
  "mode": "Synthetic worker replies; actual engine intake and validation",
  "source": "Morgan will review the fixture.",
  "public_message": "Morgan will review the fixture.",
  "short_private_version": "Review the fixture.",
  "completed_stages": {
    "mapping": "mapping",
    "public_safety": "redactions",
    "original_versions": "versions",
    "public_versions": "result_pointer",
    "discord_original": "source_pointer",
    "discord_public": "result_pointer"
  },
  "worker_replies": [
    "original_versions",
    "mapping",
    "public_safety"
  ],
  "live_model_calls": 0
}
```

Generated timestamps and synthetic identifiers can change between runs. The demonstrated behavior and input fixture remain inspectable in the adjacent example files.
