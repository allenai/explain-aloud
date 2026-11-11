# explain-aloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Claude Code plugin whose Stop hook rewrites each qualifying reply into a teacher-style spoken explanation (via headless `claude -p --model haiku`) and plays it through local TTS, with pause/resume/seek-back control.

**Architecture:** One stdlib-only Python script (`scripts/explain.py`) vendored from read-aloud 0.4.0's `speak.py` (MIT) and extended with: transcript exchange extraction, trigger heuristic, Haiku script generation, mpv playback control, and a cancel path driven by a UserPromptSubmit hook. Two hooks in `hooks/hooks.json`; slash commands are thin SKILL.md wrappers around the script's CLI.

**Tech Stack:** Python 3.9+ stdlib (runtime), pytest (dev only), mpv (optional, enables control), Kokoro TTS via read-aloud's existing install, `claude` CLI.

**Spec:** `docs/superpowers/specs/2026-11-11-explain-aloud-design.md`

## Global Constraints

- Runtime code: Python 3.9+, standard library only. pytest is dev-only.
- Linux is the supported platform; keep vendored mac/win code paths, don't test them.
- Vendored code keeps read-aloud's MIT attribution (LICENSE note + header comment).
- Hook entries must exit 0 on any failure; Stop hook timeout 15s, UserPromptSubmit 5s.
- Pidfile path is read-aloud's: `$TMPDIR/claude-read-aloud.pid` (cross-plugin mutual exclusion).
- Voice/provider/speed read from `~/.config/claude-read-aloud/config.json`; own config at `~/.config/explain-aloud/config.json`; shipped `mode` default is `"always"`.
- Explanation model default: `haiku` (CLI alias), invoked as `claude -p --model haiku`.
- Recursion sentinel env var: `EXPLAIN_ALOUD_CHILD=1`.
- Fixed failure line (exact copy): `Couldn't prepare an explanation — the answer is on screen.`
- All commits end with the `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` footer.

## Review Focus

- Reply consisting only of tool calls / whitespace → no audio, exit 0 (test in Task 2).
- read-aloud config absent or corrupt JSON → defaults used, no crash (test in Task 1).
- `claude` CLI not on PATH in the detached worker → failure line spoken, no exception (test in Task 4).
- Haiku output wrapped in markdown/code fences, or prefixed with the user's global `[HH:MM]` timestamp rule leaking from CLAUDE.md, or empty → sanitized, empty → failure line (test in Task 4).
- Transcript containing non-JSON lines or malformed entries → parser skips them, still finds the last exchange (test in Task 2).

---

### Task 1: Scaffold, vendor, config layering

**Files:**
- Create: `.claude-plugin/plugin.json`, `scripts/explain.py` (vendored copy), `LICENSE`, `tests/test_config.py`, `tests/conftest.py`, `.gitignore`

**Interfaces:**
- Produces: `load_cfg() -> dict` — merged config later tasks read. Keys: `mode` ("always"|"auto"|"off", default "always"), `model` (default "haiku"), `timeout_s` (default 60), `max_speech_words` (default 250), `context_cap_chars` (default 4000), `auto_min_chars` (default 700), `auto_min_bullets` (default 6), plus `provider`, `voice`, `speed`, `first_chars`, `chunk_chars`, `max_chars` taken from read-aloud's config (its defaults when that file is absent).
- Produces: module-level constant `FALLBACK_LINE` with the exact failure line.

- [ ] **Step 1: Scaffold.** Copy `/home/aryeht/.claude/plugins/cache/anthropic-plugin-directory/read-aloud/0.4.0-c69092562a0b/scripts/speak.py` to `scripts/explain.py`; copy that plugin's LICENSE to `LICENSE` and add a line naming the vendored file. Write `plugin.json` (name `explain-aloud`, version `0.1.0`, description one line). `.gitignore`: `__pycache__/`, `.pytest_cache/`.
- [ ] **Step 2: Write failing tests** in `tests/test_config.py` (conftest adds `scripts/` to `sys.path`):

```python
def test_defaults_when_no_config_files(tmp_path, monkeypatch):
    # point both config paths into empty tmp_path
    cfg = explain.load_cfg()
    assert cfg["mode"] == "always" and cfg["model"] == "haiku"
    assert cfg["timeout_s"] == 60 and cfg["context_cap_chars"] == 4000

def test_voice_comes_from_read_aloud_config(tmp_path, monkeypatch):
    # write read-aloud config {"provider":"kokoro","voice":"am_onyx","speed":0.8}
    cfg = explain.load_cfg()
    assert (cfg["provider"], cfg["voice"], cfg["speed"]) == ("kokoro", "am_onyx", 0.8)

def test_corrupt_read_aloud_config_falls_back(tmp_path, monkeypatch):
    # write invalid JSON to the read-aloud config path
    cfg = explain.load_cfg()
    assert cfg["provider"] == "system"  # read-aloud's own default
```

Make config paths injectable for tests (env override or module function monkeypatched — implementer's choice, but both files' paths must be patchable independently).

- [ ] **Step 3: Run tests, verify FAIL** (`load_cfg` not defined).
- [ ] **Step 4: Implement in `scripts/explain.py`:** replace vendored `DEFAULTS`/`load_config` with `load_cfg()` per the Interfaces block; rename scratch-audio names `claude-read-aloud-*` → `claude-explain-aloud-*` (glob too); KEEP `PIDFILE` as `claude-read-aloud.pid`. Define `FALLBACK_LINE`. Delete vendored `--auto`, `--setup`, kokoro-setup and voice-listing CLI paths (read-aloud owns voice management); keep their support functions used by playback.
- [ ] **Step 5: Run tests, verify PASS.**
- [ ] **Step 6: Commit** `feat: scaffold plugin, vendor TTS core, layered config`.

### Task 2: Transcript exchange extraction + trigger heuristic

**Files:**
- Modify: `scripts/explain.py`
- Test: `tests/test_extract.py`, `tests/test_trigger.py`

**Interfaces:**
- Produces: `last_exchange(path: pathlib.Path, cap: int) -> tuple[str, str]` — (last user message text truncated to `cap` chars, final assistant text block); `("", "")` parts when absent.
- Produces: `should_speak(reply: str, cfg: dict) -> bool`.

- [ ] **Step 1: Write failing tests.** `test_extract.py`: build jsonl fixtures (reuse read-aloud transcript shape: `{"type":"assistant","message":{"content":[{"type":"text","text":...}]}}`, user lines `{"type":"user","message":{"content":[{"type":"text","text":...}]}}`; a user message may also be a plain string content):

```python
def test_returns_last_user_msg_and_last_reply(tmp_path): ...
def test_user_msg_truncated_to_cap(tmp_path): ...          # cap=10 → 10 chars kept (tail dropped)
def test_skips_malformed_and_non_json_lines(tmp_path): ... # garbage lines between valid ones
def test_tool_only_reply_yields_empty_reply(tmp_path): ... # assistant entry with no text blocks
```

`test_trigger.py`:

```python
def test_mode_off_never_speaks(): ...
def test_mode_always_speaks_nonempty_and_skips_empty(): ...   # "" and "   " → False even in always
def test_auto_short_prose_silent(): ...                       # 200 chars, no table/code/bullets → False
def test_auto_long_prose_speaks(): ...                        # > auto_min_chars → True
def test_auto_table_or_code_fence_speaks(): ...               # short text containing "|...|" row or ``` → True
def test_auto_many_bullets_speaks(): ...                      # >= auto_min_bullets lines starting "- " → True
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement both functions.** Auto heuristic: speak when any signal fires — stripped prose length > `auto_min_chars`, a markdown table row exists, a code fence exists, or bullet count ≥ `auto_min_bullets`.
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit** `feat: exchange extraction and trigger heuristic`.

### Task 3: Haiku script generation

**Files:**
- Modify: `scripts/explain.py`
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: `FALLBACK_LINE`, `load_cfg()`.
- Produces: `build_prompt(user_msg: str, reply: str, cfg: dict) -> str`; `sanitize_script(s: str) -> str`; `generate_script(user_msg: str, reply: str, cfg: dict) -> str` (never raises; returns `FALLBACK_LINE` on any failure/empty).

- [ ] **Step 1: Write failing tests** (mock `subprocess.run` via monkeypatch):

```python
def test_invokes_claude_cli_with_model_and_sentinel(): ...  # argv starts ["claude","-p","--model","haiku"]; env has EXPLAIN_ALOUD_CHILD="1"
def test_timeout_returns_fallback_line(): ...               # subprocess raises TimeoutExpired
def test_missing_cli_returns_fallback_line(): ...           # raises FileNotFoundError
def test_empty_output_returns_fallback_line(): ...
def test_sanitize_strips_fences_timestamp_and_markdown(): ...
    # "[12:34]\n```\nHello there\n```" -> "Hello there"; also strips **bold**, `code`, leading "- "
def test_prompt_contains_reply_user_msg_and_word_budget(): ...
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.** `generate_script` runs `["claude", "-p", "--model", cfg["model"]]` with the prompt on stdin, `timeout=cfg["timeout_s"]`, env = os.environ + `EXPLAIN_ALOUD_CHILD=1`, then `sanitize_script`. The prompt (exact copy, `{...}` interpolated):

```
You turn a coding assistant's reply into a SPOKEN explanation, like a teacher
explaining a slide rather than reading it. Your output goes directly to
text-to-speech, so output ONLY the words to be spoken: plain text, no
markdown, no headings, no timestamps, no preamble.

Rules:
- The listener must be able to act without reading the screen. Lead with the
  conclusion, then what to do.
- Natural spoken rhythm, short sentences.
- Unpack terse references: name the thing being referred to and explain it as
  if the listener hasn't parsed the jargon in the text. Use the user's
  question below to resolve references like "option 2".
- If the reply contains a table or diagram, do NOT read it; point at it
  instead, e.g. "the table on screen — the third row is the problem", and say
  what to look for in it.
- Never recite file paths, CLI flags, arrows, URLs, or code. Describe what
  code does. Shorten a path to its last meaningful name ("the explain script").
- Length proportional to the reply: at most {max_speech_words} words; a short
  reply gets a few sentences.

The user's question was:
{user_msg}

The assistant's reply to explain:
{reply}
```

`sanitize_script`: strip code fences, a leading `[HH:MM]` line, markdown emphasis/backticks/bullet markers, collapse whitespace.

- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit** `feat: haiku spoken-script generation with sanitizer and fallback`.

### Task 4: Hooks — Stop pipeline, cancel, recursion guard

**Files:**
- Create: `hooks/hooks.json`
- Modify: `scripts/explain.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `last_exchange`, `should_speak`, `generate_script`, vendored `claim`/`stop`/`detach`/playback.
- Produces: CLI modes `--hook`, `--cancel`, `--worker --transcript PATH`; worker pidfile `$TMPDIR/claude-explain-aloud-worker.pid`.

- [ ] **Step 1: Write failing tests** (call mode functions directly with monkeypatched collaborators; plus one subprocess test):

```python
def test_hook_exits_zero_on_garbage_stdin(): ...        # run script via subprocess: echo "not json" | explain.py --hook → rc 0
def test_hook_skips_when_sentinel_env_set(): ...        # EXPLAIN_ALOUD_CHILD=1 → detach not called
def test_hook_skips_when_mode_off(): ...
def test_hook_skips_and_warns_when_read_aloud_auto_read_on(capsys): ...  # stderr warning, detach not called
def test_hook_detaches_worker_with_transcript_path(): ...
def test_cancel_kills_worker_pidfile_and_playback(): ...# writes fake worker pid, cancel sends SIGTERM + calls stop()
def test_worker_writes_pidfile_before_generation(): ... # order: pidfile exists when (mocked) generate_script runs
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.** `--hook`: hardened like vendored `hook_mode` (stdin JSON → `transcript_path`; any exception → 0), plus sentinel and mode checks and the read-aloud `auto_read` check (on → warn to stderr, skip, exit 0 — never race its reading), detaches `--worker --transcript PATH`. Worker sequence: write worker pidfile (with SIGTERM handler → exit) → `last_exchange` → `should_speak` → `generate_script` → claim+speak → remove worker pidfile. Claim happens only at speak time, AFTER generation; cancel killing via worker pidfile is what prevents a stale explanation outliving a newer prompt. `--cancel`: SIGTERM pid in worker pidfile (if alive), then vendored `stop()`, always exit 0.
- [ ] **Step 4: Write `hooks/hooks.json`:** Stop → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py" --hook` timeout 15; UserPromptSubmit → same with `--cancel` timeout 5 (both with the `|| python …` fallback pattern read-aloud uses).
- [ ] **Step 5: Run, verify PASS.**
- [ ] **Step 6: Commit** `feat: stop/cancel hooks with recursion guard and worker lifecycle`.

### Task 5: mpv playback control

**Files:**
- Modify: `scripts/explain.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Produces: CLI `--pause`, `--resume`, `--back [SECONDS]` (default 10); `MPV_SOCKET = $TMPDIR/claude-explain-aloud-mpv.sock`; `ipc_payload(action: str, seconds: float = 10) -> dict`.

- [ ] **Step 1: Write failing tests:**

```python
def test_ipc_payloads(): ...
    # pause -> {"command":["set_property","pause",True]}
    # resume -> {"command":["set_property","pause",False]}
    # back -> {"command":["seek",-10,"relative"]}
def test_player_cmd_prefers_mpv_with_ipc_socket(monkeypatch): ...  # which("mpv") hit → argv has --input-ipc-server=…--no-video
def test_player_cmd_falls_back_without_mpv(monkeypatch): ...        # vendored aplay/paplay path intact
def test_control_without_socket_prints_hint(capsys): ...            # no socket file → message mentions mpv, rc 0
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.** Extend vendored `player_cmd` to prefer mpv (`mpv --really-quiet --no-video --input-ipc-server=SOCK file.wav`); control modes connect to the unix socket (stdlib `socket`), send one JSON line. Pause state spans chunks: controls act on current chunk's mpv; `--back` seeks within the current chunk (floor at its start) — cross-chunk rewind is out of scope, say so in `--back`'s help text. Stale socket removed on new playback.
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Commit** `feat: mpv player with pause/resume/seek-back IPC`.

### Task 6: Slash commands, status, README

**Files:**
- Create: `skills/explain/SKILL.md`, `skills/mode/SKILL.md`, `skills/status/SKILL.md`, `skills/stop/SKILL.md`, `skills/pause/SKILL.md`, `skills/resume/SKILL.md`, `skills/back/SKILL.md`, `README.md`
- Modify: `scripts/explain.py` (`--status`, `--mode`, `--print`)

**Interfaces:**
- Consumes: the CLI surface from Tasks 4–5.
- Produces: `--mode always|auto|off` (persists to own config), `--status` (mode, model, thresholds, voice source, mpv availability, warning when read-aloud `auto_read` is on), `--print` (print generated script for the newest transcript, speak nothing).

- [ ] **Step 1: Implement the three CLI additions** (no new pure logic worth a unit test beyond: one test in `tests/test_config.py` that `--mode auto` persists and `--status` output contains a `WARNING` line when read-aloud config has `"auto_read": true`; write that test first, watch it fail, then implement).
- [ ] **Step 2: Write the seven SKILL.md files.** Each: frontmatter (name, description with trigger phrases) + body instructing Claude to run the matching `explain.py` flag via Bash with `${CLAUDE_PLUGIN_ROOT}`. `explain` = speak an explanation of the last reply now (`--transcript` from hook-free path, i.e. newest transcript of this project); `mode` takes the argument through to `--mode`.
- [ ] **Step 3: README:** what it is, install (marketplace add from local path), config keys table, control commands, "read-aloud auto_read must stay off", mpv note.
- [ ] **Step 4: Run full test suite, verify PASS.** `python3 -m pytest tests/ -q`
- [ ] **Step 5: Commit** `feat: slash commands, status/mode/print, README`.

### Task 7: Install + manual end-to-end verification

**Files:**
- Create: `.claude-plugin/marketplace.json` (single-plugin local marketplace, source `.`)

- [ ] **Step 1: Validate plugin:** `claude plugin validate .` → no errors (if the subcommand is unavailable, lint hooks.json/plugin.json shapes by hand against read-aloud's).
- [ ] **Step 2: Dry-run the pipeline without audio:** `python3 scripts/explain.py --print` in a project with a real transcript → prints a plain-text spoken script; verify no markdown, no timestamp, references unpacked.
- [ ] **Step 3: Manual audio test:** `echo '{"transcript_path":"<fixture>"}' | python3 scripts/explain.py --hook` with mode `always` → explanation audio plays within ~15s of generation finishing; `--pause`, `--resume`, `--back` work mid-speech; second hook invocation cancels the first cleanly.
- [ ] **Step 4: Install for real:** `/plugin marketplace add /home/aryeht/PycharmProjects/explain-aloud` + install (user does this in the app; document in README if CLI differs).
- [ ] **Step 5: Live session check:** ask any Claude session a question producing a long reply → explanation speaks; send next prompt mid-speech → audio stops.
- [ ] **Step 6: Commit** `feat: local marketplace manifest` + tag `v0.1.0`.
