# explain-aloud

> ## [🎙️ &nbsp;TRY ME BEFORE READING&nbsp; →](https://claude.ai/artifact/T1brUMtBHNviEaYJEdDPR7)
>
> Thirty seconds, sound on: a mock Claude chat that explains this plugin's
> motivation the way the plugin would — out loud, with the real buttons.
>
> Just want the audio? **[▶️ Play the 30-second pitch](assets/pitch.mp4)**
> (GitHub opens it with a built-in player.)

A Claude Code plugin that **explains replies aloud like a teacher** instead of
reading them verbatim. After each qualifying reply, a cheap model (`claude -p
--model haiku`, your subscription) rewrites it into a spoken explanation —
self-sufficient enough to act on without reading, pointing at tables instead
of reciting them, unpacking terse jargon — and plays it through local TTS.

Built on top of the [read-aloud](https://github.com/michaelpgifford/claude-read-aloud)
plugin's MIT-licensed TTS core (vendored): Kokoro local neural voice, chunked
low-latency playback. read-aloud stays the place you pick your voice.

## Install (2 minutes)

**Ai2 folks — one click**: add it from the organization's plugin library:
<https://claude.ai/customize/plugins/plugin_01Jtm4zwTbiWuNwTfFp6pFp3>
(or in Claude Code: `/plugin` → Marketplaces → add the organization library →
`/plugin install explain-aloud@claudeai-organization-library`).

From GitHub instead, in any Claude Code session (desktop app or CLI):

```
/plugin marketplace add allenai/explain-aloud
/plugin install explain-aloud@explain-aloud
```

Either way, finish with:

```
/read-aloud:voice-setup
```

That installs the free local neural voice (~340MB one-time download) via the
read-aloud plugin — install that first from the plugin directory if you don't
have it (`/plugin install read-aloud`). Optionally `sudo apt install mpv` to
enable pause/resume/rewind and the live progress percentage. Restart Claude
Code.

## Use

- **`/explain-aloud:explain`** — speak a teacher-style explanation of the last
  reply. Generation takes ~15–40s, then audio starts.
- **Button lane** (desktop app and terminal) — a slim row above the prompt box:
  🎙️ Explain · ⏪ 5s · ⏸ Pause · ⏹ Stop, plus a live status at the end of the
  row: `🧠 writing script… 12s` while the model works, `🔊 64%` during
  playback. Buttons that don't apply to the current phase are dimmed and
  inert, so the row never changes shape.
- Sending a new prompt — or closing the session — cancels any pending or
  playing explanation.
- Default mode is on-demand (`off`); `/explain-aloud:mode always|auto` makes it
  speak after every / after long replies.
- Default voice comes from read-aloud's config (`af_heart` if you followed the
  setup); change it with `/read-aloud:voice`.

Installing from a local checkout instead: `/plugin marketplace add <path-to-repo>`.

Requirements:
- Linux, Python 3.9+ (stdlib only at runtime).
- The read-aloud plugin's Kokoro voice installed (`/read-aloud:voice-setup`)
  — or any provider configured there; system voice works out of the box.
- `claude` CLI on PATH (used headless for script generation).
- `mpv` (optional) — enables pause / resume / seek-back and the playback
  percentage. Without it audio still plays through aplay/paplay, just without
  controls.

**read-aloud's `auto_read` must stay off** — explain-aloud detects it and
skips speaking rather than letting two voices race.

## Commands

| Command | Does |
|---|---|
| `/explain-aloud:explain` | explain the last reply now |
| `/explain-aloud:mode always\|auto\|off` | when to speak (default: off = on-demand) |
| `/explain-aloud:model haiku\|sonnet\|opus` | which model writes the explanation (default haiku; bigger = better, slower) |
| `/explain-aloud:status` | current config + warnings |
| `/explain-aloud:stop` | stop/cancel current explanation |
| `/explain-aloud:pause` / `resume` | pause / resume (needs mpv) |
| `/explain-aloud:back [s]` | seek back, default 10s (needs mpv) |

## Config

`~/.config/explain-aloud/config.json` (all optional):

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"off"` | `off` (on-demand) / `auto` (threshold) / `always` |
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
`claude -p --model haiku` (headless, no MCP servers) writes a teacher-style
spoken script → chunked TTS playback. Detached and hardened: a failure never
blocks Claude; on generation failure one fixed line is spoken instead of
silence. Sending a new prompt cancels pending generation and playback
(UserPromptSubmit hook), and ending the session does too (SessionEnd hook).
Workers register per-pid files so concurrent sessions can't strand a stale
explanation. A sentinel env var keeps the plugin's own headless calls from
re-triggering its hooks. While running, the worker writes a status file
(`generating` with a start time, then `speaking` with chars done/total,
refined every second from mpv's playback position); the button band polls it
for the live status and percentage.
