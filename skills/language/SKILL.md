---
description: Set the spoken-explanation language (e.g. hebrew; english = default)
---

The user passes a language name (e.g. `hebrew`, `english`). Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --set-language <argument>
```

Relay the command's output. If no argument was given, run the same script
with `--status` and relay its output instead. A non-English language needs a
cloud TTS voice (the local Kokoro voice is English-etc. only); the command
picks one automatically when an `elevenlabs` or `openai` API key is present
under `api_keys` in `~/.config/claude-read-aloud/config.json`, and otherwise
says what to add. If `python3` is not found, retry with `python`.
