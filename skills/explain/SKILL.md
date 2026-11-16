---
description: Explain Claude's last reply aloud, teacher-style (not a verbatim read-out); pass N to explain the reply N exchanges back
---

The user may pass one number N (e.g. `2` = the reply before the last one,
`3` = two before that). No argument means 1, the last reply. Run exactly this
one bash command, putting that number in place of `N`, then reply with
"🎙️ explaining aloud — generating the spoken script takes about 15–40 seconds, audio follows"
(when N > 1, add which one: e.g. "the 2nd-last reply"):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --worker --force --nth N --session-id "${CLAUDE_CODE_SESSION_ID}" --project "$(pwd)" --detach
```

If `python3` is not found, retry the same command with `python`.
