# explain-aloud

A Claude Code plugin that **explains replies aloud like a teacher** instead of
reading them verbatim. After each qualifying reply, a cheap model (`claude -p
--model haiku`, your subscription) rewrites it into a spoken explanation —
self-sufficient enough to act on without reading, pointing at tables instead
of reciting them, unpacking terse jargon — and plays it through local TTS.

Built on top of the [read-aloud](https://github.com/michaelpgifford/claude-read-aloud)
plugin's MIT-licensed TTS core (vendored): Kokoro local neural voice, chunked
low-latency playback. read-aloud stays the place you pick your voice.

## Install

```
/plugin marketplace add /home/aryeht/PycharmProjects/explain-aloud
/plugin install explain-aloud@explain-aloud
```

Requirements:
- Linux, Python 3.9+ (stdlib only at runtime).
- The read-aloud plugin's Kokoro voice installed (`/read-aloud:voice-setup`)
  — or any provider configured there; system voice works out of the box.
- `claude` CLI on PATH (used headless for script generation).
- `mpv` (optional) — enables pause / resume / seek-back. Without it audio
  still plays through aplay/paplay, just without controls.

**read-aloud's `auto_read` must stay off** — explain-aloud detects it and
skips speaking rather than letting two voices race.

## Commands

| Command | Does |
|---|---|
| `/explain-aloud:explain` | explain the last reply now |
| `/explain-aloud:mode always\|auto\|off` | when to speak (default: always) |
| `/explain-aloud:status` | current config + warnings |
| `/explain-aloud:stop` | stop/cancel current explanation |
| `/explain-aloud:pause` / `resume` | pause / resume (needs mpv) |
| `/explain-aloud:back [s]` | seek back, default 10s (needs mpv) |

## Config

`~/.config/explain-aloud/config.json` (all optional):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"always"` | `always` / `auto` (threshold) / `off` |
| `model` | `"haiku"` | claude CLI model alias for generation |
| `timeout_s` | `60` | generation timeout; on failure one fixed line is spoken |
| `max_speech_words` | `250` | word budget for the spoken script |
| `context_cap_chars` | `4000` | how much of your question is sent for context |
| `auto_min_chars` | `700` | auto mode: prose length trigger |
| `auto_min_bullets` | `6` | auto mode: bullet count trigger |

Voice, provider and speed are read from read-aloud's config
(`~/.config/claude-read-aloud/config.json`) so the voice is picked in one place.

## How it works

Stop hook → extract last exchange from the transcript → trigger check →
`claude -p --model haiku` writes a teacher-style spoken script → chunked TTS
playback. Detached and hardened: a failure never blocks Claude. Sending a new
prompt cancels pending generation and playback (UserPromptSubmit hook). A
sentinel env var keeps the plugin's own headless calls from re-triggering it.
