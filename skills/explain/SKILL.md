---
description: Explain Claude's last reply aloud, teacher-style (not a verbatim read-out)
---

Run exactly this one bash command, then reply with "🎙️ explaining aloud":

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --worker --project "$(pwd)" --detach
```

If `python3` is not found, retry the same command with `python`.
