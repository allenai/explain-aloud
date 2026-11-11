---
description: Seek the spoken explanation back a few seconds (default 10)
---

The user may pass a number of seconds (default 10). Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --back <seconds-or-nothing>
```

Relay any output. Note: seeking works within the current audio chunk only.
If `python3` is not found, retry with `python`.
