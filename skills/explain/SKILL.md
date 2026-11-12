---
description: Explain Claude's last reply aloud, teacher-style (not a verbatim read-out)
---

Run exactly this one bash command, then reply with "🎙️ explaining aloud — generating the spoken script takes about 15–40 seconds, audio follows":

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --worker --force --session-id "${CLAUDE_CODE_SESSION_ID}" --project "$(pwd)" --detach
```

If `python3` is not found, retry the same command with `python`.
