---
description: Set which Claude model writes the spoken explanations (haiku, sonnet, opus)
---

The user passes one argument: a claude CLI model alias such as `haiku`,
`sonnet`, or `opus`. Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --set-model <argument>
```

Relay the command's output. If no argument was given, run the same script
with `--status` and relay its output instead. Note to the user: a bigger
model writes better explanations but takes longer before audio starts.
If `python3` is not found, retry with `python`.
