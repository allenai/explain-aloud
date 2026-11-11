---
description: Set how many earlier thread messages the explanation gets as context (0 = none)
---

The user passes one number (e.g. `0`, `6`, `12`). Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --set-context <argument>
```

Relay the command's output. If no argument was given, run the same script
with `--status` and relay its output instead. Note to the user: more context
resolves more cross-turn references but makes generation slightly slower.
If `python3` is not found, retry with `python`.
