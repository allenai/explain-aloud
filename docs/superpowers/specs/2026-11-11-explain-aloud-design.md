# explain-aloud — design

Date: 2026-11-11. Decided in a brainstorming conversation between Aryeh and Claude.

## Intent and why

Working with coding agents has shifted Aryeh's day from writing code to
talking, brainstorming, deciding, and instructing — which means reading agent
answers constantly. Walls of text are paralyzing, especially when the prose is
cryptic: terse sentences that refer to something hard to understand without
unpacking it. Existing read-aloud tooling repeats the text verbatim through
regex markdown stripping — it reads the slide instead of explaining it.

explain-aloud is a Claude Code plugin: after a reply ends, a cheap model
rewrites the reply into a teacher-style spoken explanation and plays it
through local TTS. The listener should be able to act on the answer **without
reading the text at all**. Where the reply contains visual aids (tables,
diagrams), the audio points the listener at them ("the table on screen —
third row is the problem") instead of reading them.

## Goals

- After each qualifying Claude Code reply, play a spoken explanation
  self-sufficient enough to act on without reading.
- Unpack cryptic prose: name the thing a terse reference points at, assume
  the listener hasn't parsed the jargon.
- Visual aids are pointed at, never read aloud.
- Playback control: pause, resume, seek back a few seconds.
- Trigger modes: `always` / `auto` (length-complexity threshold) / `off`.
  Ships with `always` so early debugging is easy; `auto` is the intended
  steady state.
- Hook must never block or break Claude Code's own flow.
- Active machine-wide, every session including headless runs (Aryeh's
  decision): no per-project allowlist; `mode: off`/`auto` is the escape
  hatch.
- Sending a new prompt cancels any pending or playing explanation
  (Aryeh's decision).

## Non-goals

- Replacing the read-aloud plugin for verbatim reading (it stays installed
  and untouched).
- Voice input or two-way audio conversation.
- Speaking partial replies mid-turn / streaming TTS (Stop hook only, v1).
- Any UI panel (v1).
- Supporting macOS/Windows in v1 (Aryeh is on Linux; keep the vendored
  cross-platform code paths but don't test them).

## Anti-goals (failure even if it technically works)

- Audio that is a mere summary you still have to read the text to act on.
- Reading markdown signals aloud: pipes, backticks, file paths, CLI flags,
  arrows, URLs.
- Added latency or a crash in the Stop hook affecting Claude Code.
- Two voices talking over each other (explain-aloud vs read-aloud).

## Constraints

- Linux desktop, audio via standard players; `mpv` required for playback
  control (graceful degradation without it: speech still plays, control
  commands report "install mpv").
- Hook/orchestrator script: Python 3.9+, standard library only (the vendored
  code is already stdlib-only).
- TTS: Kokoro local neural voice, already installed at
  `~/.local/share/claude-read-aloud` (user's current voice `am_onyx`,
  speed 0.8).
- Explanation model: headless CLI — `claude -p --model haiku` — so the user's
  subscription is used and no API key is managed.
- Vendor `speak.py` from read-aloud 0.4.0 (MIT, attribution kept): chunked
  ramped playback, pidfile claim/stop, Kokoro runner, transcript parsing.

## Decided design

### Repository / plugin layout

Standalone plugin in its own repo, `~/PycharmProjects/explain-aloud`:

- `.claude-plugin/plugin.json`
- `hooks/hooks.json` — Stop hook → `scripts/explain.py --hook`, and a
  UserPromptSubmit hook that cancels any pending generation or playing
  explanation (Aryeh's decision: a new prompt means the old answer's audio
  is stale)
- `scripts/explain.py` — orchestrator: vendored `speak.py` plus the new
  explanation pipeline (one file is fine; builder may split)
- `skills/` — slash commands: explain last reply on demand, set mode
  (`always|auto|off`), status, stop, pause, resume, back
- `docs/superpowers/specs/` — this document

### Flow

1. **Stop hook** receives the event on stdin, reads `transcript_path`
   (never guesses the session), re-launches itself detached, exits 0
   immediately. Any exception → exit 0. (Same hardened pattern as
   read-aloud's `hook_mode()`.)
2. **Extract** the final assistant text block from the transcript
   (vendored `last_reply()`).
3. **Trigger check** by mode:
   - `off` → nothing.
   - `always` → speak every non-empty reply.
   - `auto` → speak only if the reply crosses a size/complexity threshold
     (signals: prose length, presence of tables or code blocks, bullet
     count). Exact heuristic and default values: builder's call, but each
     signal's threshold must be a config key.
4. **Generate the spoken script**: run `claude -p --model haiku` with a
   fixed instruction prompt, the reply, **and the user message that
   prompted it** (my call: terse references like "option 2" or "the file we
   discussed" are unresolvable from the reply alone; the triggering user
   message recovers most of them cheaply. Context is capped — config key,
   default ~4k chars — and older turns are not sent in v1). The hook must
   guard against triggering itself recursively from its own `claude -p`
   child (sentinel env var). Binding requirements for that
   prompt (exact wording is the builder's):
   - Audio must be self-sufficient: the listener acts without reading.
   - Teacher explaining, not narrating: natural spoken rhythm, short
     sentences, lead with the conclusion.
   - Unpack terse references: name the thing referred to; assume the
     listener hasn't parsed the jargon in the text.
   - Tables/diagrams: point the listener at them, never read their
     contents ("the table on screen — third row is the problem").
   - Nothing cryptic recited: no paths, flags, arrows, URLs, markdown
     artifacts; code is described by what it does.
   - Length proportional to the reply, roughly 30–90 seconds of speech
     (~75–250 words); hard cap in config.
   - Output plain text only — it goes straight to TTS.
   Timeout (config, default ~60s). On CLI failure or timeout, speak one
   short fixed line: "Couldn't prepare an explanation — the answer is on
   screen." (my call: silent failure hides breakage; verbatim fallback
   defeats the purpose).
5. **Speak** the script through the vendored chunked-playback layer
   (first-chunk ramp keeps time-to-first-sound ~3s with Kokoro).
6. **Mutual exclusion**: reuse read-aloud's pidfile path
   (`$TMPDIR/claude-read-aloud.pid`) so starting an explanation stops any
   read-aloud reading and vice versa — the two plugins can never talk over
   each other (my call). explain-aloud never modifies read-aloud's config:
   read-aloud's `auto_read` is expected to stay off (it is off today);
   when both auto modes are detected on, explain-aloud warns in `--status`
   and at hook time, and skips speaking rather than racing (my call).

### Playback control

- Player swapped to `mpv --input-ipc-server=<socket>` when mpv exists;
  pause / resume / seek −10s / seek to start are JSON IPC commands to the
  socket.
- Exposed as a small CLI entry point (`explain.py --pause`, `--resume`,
  `--back [seconds]`) and as matching slash commands that shell out to it.
- Without mpv, playback falls back to the vendored players and the control
  commands print how to enable control (install mpv).

### Configuration

`~/.config/explain-aloud/config.json`:

- `mode`: `always` (shipped default) | `auto` | `off`
- `auto` thresholds (per-signal keys, builder names them)
- `model`: default `haiku` (the CLI alias)
- `timeout_s`, `max_speech_words`
- Voice, provider, and speed are **read from read-aloud's config**
  (`~/.config/claude-read-aloud/config.json`) so the voice is picked in one
  place; sensible defaults when that file is absent (my call).

### Left to the builder

- Exact auto-trigger heuristic and default threshold values.
- Exact wording of the Haiku prompt (requirements above are binding).
- mpv IPC details, socket lifecycle/cleanup, seek granularity.
- Slash command naming and help text.
- Whether orchestrator stays one file or splits vendored/new code.
- How `claude -p` is located and invoked from a detached process (PATH
  handling), and keeping its cost low (no tools, no MCP; e.g.
  `--strict-mcp-config` / minimal flags).
