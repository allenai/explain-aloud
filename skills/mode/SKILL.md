---
description: Set when explain-aloud speaks — always, auto (long/complex replies only), or off
---

The user passes one argument: `always`, `auto`, or `off`. Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --mode <argument>
```

Relay the command's output. If no argument was given, show the current mode
instead by running the same script with `--status` and relaying its output.
If `python3` is not found, retry with `python`.
