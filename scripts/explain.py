#!/usr/bin/env python3
"""explain-aloud: explain Claude's replies aloud like a teacher.

Vendored from read-aloud 0.4.0's speak.py (MIT, (c) 2026 Michael Gifford) and
extended. Python 3.9+, standard library only.

Inherited design notes that still hold:

* CHUNKED, RAMPED PLAYBACK. Synthesis time scales with input length — the
  first chunk's size IS the time-to-first-sound. Chunks ramp ~260 → ~1000 →
  1800 at sentence boundaries; chunk i+1 synthesises while chunk i plays.
* THE ORCHESTRATOR IS THE PROCESS YOU STOP. This process stays alive for the
  whole playback, records its pid, and SIGTERM takes down the current player
  with it. The pidfile is SHARED with read-aloud so the two plugins can never
  talk over each other.
* HOOK MODE MUST BE HARMLESS. It reads the exact transcript path from stdin
  (never guesses), spawns itself detached, and exits 0 no matter what —
  a failure here must never break Claude's own flow.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request
import wave

# --------------------------------------------------------------------------- paths

HOME = pathlib.Path.home()
PROJECTS = HOME / ".claude" / "projects"
PIDFILE = pathlib.Path(tempfile.gettempdir()) / "claude-read-aloud.pid"
# Scratch audio is named per process. Two readings overlap for a moment by
# design — the one being stopped is still winding down while its replacement
# synthesises — and one shared pair of filenames means the dying player reads
# a file the new one is rewriting. That is heard as garble, not as a handover.
PARTS = [pathlib.Path(tempfile.gettempdir()) / f"claude-explain-aloud-{os.getpid()}-{i}.wav"
         for i in (0, 1)]
PART_GLOB = "claude-explain-aloud-*-[01].wav"
# Each worker (generation + playback) registers its own pid file so a new user
# prompt can cancel it even while it waits on the model, before any claim() —
# per-pid files, because sessions run concurrently machine-wide and one shared
# file would let a stale worker escape cancellation and claim later.
WORKER_PID_DIR = pathlib.Path(tempfile.gettempdir())
WORKER_PID_GLOB = "claude-explain-aloud-worker-*.pid"


def worker_pidfile() -> pathlib.Path:
    return WORKER_PID_DIR / f"claude-explain-aloud-worker-{os.getpid()}.pid"
# mpv's IPC socket: pause/resume/seek talk to whichever chunk is playing.
MPV_SOCKET = pathlib.Path(tempfile.gettempdir()) / "claude-explain-aloud-mpv.sock"
# Progress for UIs: {"phase": "generating", "started": ts} while the model
# writes the script, {"phase": "speaking", "done": chars, "total": chars}
# per chunk while audio plays; absent when nothing is happening.
STATUS_FILE = pathlib.Path(tempfile.gettempdir()) / "claude-explain-aloud-status.json"


def write_status(**d) -> None:
    try:
        STATUS_FILE.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass


def clear_status() -> None:
    try:
        STATUS_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _speaking_status(chunks: list, i: int) -> dict:
    return {"phase": "speaking",
            "done": sum(len(c) for c in chunks[:i]),
            "total": sum(len(c) for c in chunks)}


def _interp_done(before: int, chunk_len: int, pos: float, dur: float) -> int:
    """Chars spoken so far, interpolating within the playing chunk."""
    if not dur or dur <= 0:
        return before
    frac = min(1.0, max(0.0, pos / dur))
    return int(before + frac * chunk_len)


def mpv_position() -> "tuple[float, float] | None":
    """(time-pos, duration) of the playing mpv, or None without one."""
    if not MPV_SOCKET.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(str(MPV_SOCKET))
            vals = {}
            for rid, prop in ((1, "time-pos"), (2, "duration")):
                s.sendall((json.dumps({"command": ["get_property", prop],
                                       "request_id": rid}) + "\n").encode())
            buf = b""
            while len(vals) < 2:
                piece = s.recv(4096)
                if not piece:
                    break
                buf += piece
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rid = msg.get("request_id")
                    if rid in (1, 2) and isinstance(msg.get("data"), (int, float)):
                        vals[rid] = float(msg["data"])
            if 1 in vals and 2 in vals:
                return vals[1], vals[2]
    except OSError:
        pass
    return None


def _chunk_progress_loop(before: int, chunk_len: int, total: int,
                         stop_evt: "threading.Event") -> None:
    """Once a second while a chunk plays: refine `done` from mpv's position."""
    while not stop_evt.wait(1.0):
        pd = mpv_position()
        if pd:
            write_status(phase="speaking",
                         done=_interp_done(before, chunk_len, pd[0], pd[1]),
                         total=total)

FALLBACK_LINE = "Couldn't prepare an explanation — the answer is on screen."

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"


def _config_base() -> pathlib.Path:
    if IS_WIN:
        return pathlib.Path(os.environ.get("APPDATA", HOME))
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))


def ra_config_path() -> pathlib.Path:
    """read-aloud's config: voice, provider, speed live there (one place)."""
    env = os.environ.get("READ_ALOUD_CONFIG")
    if env:
        return pathlib.Path(env)
    return _config_base() / "claude-read-aloud" / "config.json"


def config_path() -> pathlib.Path:
    """explain-aloud's own config: mode, model, thresholds."""
    env = os.environ.get("EXPLAIN_ALOUD_CONFIG")
    if env:
        return pathlib.Path(env)
    return _config_base() / "explain-aloud" / "config.json"


def data_dir() -> pathlib.Path:
    if IS_WIN:
        base = pathlib.Path(os.environ.get("LOCALAPPDATA", HOME))
    else:
        base = pathlib.Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share"))
    return base / "claude-read-aloud"


RA_DEFAULTS = {
    "provider": "system",   # system | kokoro | speechify | elevenlabs | openai | command
    "voice": "",            # provider-specific voice name/id; "" = default
    "speed": 1.0,
    "max_chars": 12000,     # hard cap; ~14 minutes of speech
    "chunk_chars": 1800,    # per-request size, under every provider's limit
    "first_chars": 260,     # first chunk is tiny: its size is the latency
    "tts_model": "",        # openai TTS only; default tts-1
    "command": "",          # custom engine: template with {text} and/or {out}
    "api_keys": {},         # optional; env vars take precedence
    "ra_auto_read": False,  # read-aloud's auto_read, surfaced for the clash warning
}

EA_DEFAULTS = {
    "mode": "off",              # off (on-demand) | auto | always
    "model": "haiku",           # claude CLI model alias for script generation
    "timeout_s": 60,            # claude -p timeout
    "max_speech_words": 250,    # word budget passed to the prompt
    "context_cap_chars": 4000,  # user-message context cap
    "auto_min_chars": 700,      # auto mode: prose length signal
    "auto_min_bullets": 6,      # auto mode: bullet count signal
    "context_messages": 6,      # earlier messages sent as prompt context
    "history_cap_chars": 3000,  # cap on that history block
}


def _read_json(p: pathlib.Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_cfg() -> dict:
    """Merged config: voice layer from read-aloud's file, our own on top."""
    cfg = dict(RA_DEFAULTS)
    ra = _read_json(ra_config_path())
    for key in ("provider", "voice", "speed", "max_chars", "chunk_chars",
                "first_chars", "command", "api_keys"):
        if key in ra:
            cfg[key] = ra[key]
    if "model" in ra:                     # read-aloud's TTS model, not ours
        cfg["tts_model"] = ra["model"]
    if "auto_read" in ra:
        cfg["ra_auto_read"] = bool(ra["auto_read"])
    cfg.update(EA_DEFAULTS)
    own = _read_json(config_path())
    for key in EA_DEFAULTS:
        if key in own:
            cfg[key] = own[key]
    return cfg


def save_cfg(updates: dict) -> None:
    """Persist explain-aloud's own keys; read-aloud's file is never written."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    own = _read_json(p)
    own.update(updates)
    p.write_text(json.dumps(own, indent=2) + "\n", encoding="utf-8")


def api_key(cfg: dict, provider: str) -> str:
    key = os.environ.get(f"{provider.upper()}_API_KEY") \
        or cfg.get("api_keys", {}).get(provider, "")
    if not key:
        sys.exit(f"{provider} needs an API key: set {provider.upper()}_API_KEY "
                 f"or api_keys.{provider} in {config_path()}")
    return key

# ----------------------------------------------------------------- transcript → text


def newest_transcript(project: str | None = None) -> pathlib.Path | None:
    """Newest session transcript, scoped to one project when a path is given.

    Claude Code encodes a project path into its directory name by replacing
    every non-alphanumeric character with '-'. Unscoped "newest across all
    projects" reads whichever session wrote last — the wrong one whenever two
    sessions are running, which is why scoping exists.
    """
    files: list[pathlib.Path] = []
    if project:
        enc = "-" + re.sub(r"[^A-Za-z0-9]", "-", project.strip("/\\"))
        files = list((PROJECTS / enc).glob("*.jsonl"))
    if not files:
        files = list(PROJECTS.glob("*/*.jsonl"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def last_reply(path: pathlib.Path) -> str:
    """The final assistant text block in a session transcript."""
    text = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "assistant":
            continue
        blocks = e.get("message", {}).get("content", [])
        if not isinstance(blocks, list):
            continue
        joined = "".join(b.get("text", "") for b in blocks
                         if isinstance(b, dict) and b.get("type") == "text")
        if joined.strip():
            text = joined
    return text


def _entry_text(entry: dict) -> str:
    """Joined text blocks of one transcript entry; '' when there are none."""
    content = entry.get("message", {}).get("content", [])
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(b.get("text", "") for b in content
                   if isinstance(b, dict) and b.get("type") == "text")


def last_exchange(path: pathlib.Path, cap: int) -> tuple[str, str]:
    """(last user message truncated to cap chars, final assistant text block).

    Empty strings for whichever side is absent; malformed lines are skipped.
    """
    user_msg = reply = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", ""
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict) or not isinstance(e.get("message"), (dict,)):
            continue
        text = _entry_text(e)
        if e.get("type") == "user" and text.strip():
            user_msg = text
            reply = ""                   # a newer exchange started
        elif e.get("type") == "assistant" and text.strip():
            reply = text
    return user_msg[:cap], reply


def recent_exchanges(path: pathlib.Path, k: int, cap: int) -> str:
    """The last k text messages BEFORE the final user/assistant pair, as
    'User: …' / 'Assistant: …' lines, each message truncated, whole block
    capped — context so terse references in the reply resolve."""
    if k <= 0:
        return ""
    msgs: list = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict) or not isinstance(e.get("message"), dict):
            continue
        text = _entry_text(e).strip()
        role = e.get("type")
        if text and role in ("user", "assistant"):
            msgs.append(("User" if role == "user" else "Assistant", text))
    # Drop the final exchange: it is passed to the prompt separately.
    while msgs and msgs[-1][0] == "Assistant":
        msgs.pop()
    if msgs:
        msgs.pop()                        # the final user message
    out: list = []
    for role, text in msgs[-k:]:
        out.append(f"{role}: {text[:500]}")
    return "\n".join(out)[-cap:] if cap > 0 else ""


def should_speak(reply: str, cfg: dict) -> bool:
    """Trigger check: off/always, or auto's any-signal-fires heuristic."""
    mode = cfg.get("mode", "always")
    if mode == "off" or not reply.strip():
        return False
    if mode == "always":
        return True
    # auto: any signal fires
    if "```" in reply:
        return True
    if re.search(r"^\s*\|.+\|\s*$", reply, flags=re.M):
        return True
    bullets = len(re.findall(r"^[ \t]*[-*+] ", reply, flags=re.M))
    if bullets >= int(cfg.get("auto_min_bullets", 6)):
        return True
    prose = re.sub(r"\s+", " ", reply).strip()
    return len(prose) > int(cfg.get("auto_min_chars", 700))

# ------------------------------------------------------------ script generation

PROMPT_TEMPLATE = """\
You turn a coding assistant's reply into a SPOKEN explanation — a teacher
explaining the slide, never reading it. Your words go straight into
text-to-speech and will be HEARD, not seen. Write for the ear.

Voice and rhythm:
- Conversational spoken English, like a sharp colleague explaining at your
  desk. Contractions are good. Short sentences. Never written-document tone,
  never a narrator summarizing ("the assistant explains that…") — speak the
  content itself, to the listener, as "you".
- The first sentence is the bottom line. Then what to do. Then only details
  that change what the listener does next. Skip pleasantries and hedging.
- Absolutely no lists, headings, markdown, symbols, or timestamps. Several
  points chain in speech: "three things matter here. First… second…".
- Say numbers and names like a person: "about forty files", "version two
  point one", "around fifteen seconds".

Content:
- The listener acts without reading the screen; the audio must be enough.
- Unpack terse references: name the thing being referred to and explain it as
  if the listener hasn't parsed the jargon. Use the user's question below to
  resolve references like "option 2".
- If the reply contains a table or diagram, do NOT read it; point at it:
  "the table on screen — the third row is the problem", and say what to look
  for in it.
- Never recite file paths, CLI flags, arrows, URLs, or code. Describe what
  code does. Shorten a path to its last meaningful name ("the explain script").
- Length proportional to the reply: at most {max_speech_words} words; a short
  reply gets two or three sentences.

Output ONLY the words to be spoken. No preamble, no quotes, nothing else.

{history_block}The user's question was:
{user_msg}

The assistant's reply to explain:
{reply}
"""

HISTORY_BLOCK = """\
Earlier conversation (context only — resolve references with it, but explain
the reply below, not this history):
{history}

"""


def build_prompt(user_msg: str, reply: str, cfg: dict, history: str = "") -> str:
    return PROMPT_TEMPLATE.format(
        max_speech_words=int(cfg.get("max_speech_words", 250)),
        history_block=HISTORY_BLOCK.format(history=history) if history else "",
        user_msg=user_msg or "(not available)",
        reply=reply)


def sanitize_script(s: str) -> str:
    """Model output → TTS-safe plain text, whatever the model did anyway."""
    s = re.sub(r"^\s*\[\d{1,2}:\d{2}\]\s*", "", s)      # leaked [HH:MM] stamp
    s = re.sub(r"^```[^\n]*$", " ", s, flags=re.M)      # fence lines
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"^[ \t]*[-*+]\s+", "", s, flags=re.M)   # bullet markers
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)        # headings
    s = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", s)  # emphasis
    s = re.sub(r"\s+", " ", s).strip()
    return s


def generate_script(user_msg: str, reply: str, cfg: dict, history: str = "") -> str:
    """Spoken script via headless claude CLI. Never raises; FALLBACK_LINE on
    any failure — silence would hide breakage, verbatim would defeat the point."""
    env = dict(os.environ)
    env["EXPLAIN_ALOUD_CHILD"] = "1"    # our own Stop hook must ignore this run
    try:
        r = subprocess.run(
            # --strict-mcp-config with no --mcp-config: zero MCP servers load,
            # keeping the headless call cheap and fast.
            ["claude", "-p", "--model", str(cfg.get("model", "haiku")),
             "--strict-mcp-config"],
            input=build_prompt(user_msg, reply, cfg, history),
            capture_output=True, text=True, env=env,
            timeout=float(cfg.get("timeout_s", 60)), **quiet_win())
        if getattr(r, "returncode", 1) != 0:
            return FALLBACK_LINE
        script = sanitize_script(r.stdout or "")
        return script or FALLBACK_LINE
    except Exception as e:               # noqa: BLE001 — must never raise
        print(f"explain-aloud: generation failed: {e}", file=sys.stderr)
        return FALLBACK_LINE


def spoken_form(md: str, max_chars: int) -> str:
    """Markdown reads terribly aloud — strip everything that is not prose."""
    s = re.sub(r"```.*?```", " (code omitted) ", md, flags=re.S)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)      # keep link text, drop URL
    s = re.sub(r"^\s*\|.*\|\s*$", " ", s, flags=re.M)   # tables
    s = re.sub(r"^[ \t]*[-*+]\s+", "", s, flags=re.M)   # bullets
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)        # headings
    s = re.sub(r"[*_>#`]+", "", s)
    s = re.sub(r"https?://\S+", " link ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        cut = s[:max_chars]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        s = (cut[:end + 1] if end > max_chars // 2 else cut) \
            + " … I'll stop there — the rest is on screen."
    return s

# ------------------------------------------------------------------------ chunking


def chunk_text(s: str, first: int, full: int) -> list[str]:
    """Split at sentence boundaries; sizes ramp first → 4×first → full."""
    def limits():
        yield min(first, full)
        yield min(4 * first, full)
        while True:
            yield full

    gen = limits()
    limit = next(gen)
    parts: list[str] = []
    cur = ""
    for sent in re.split(r"(?<=[.!?;:]) +", s):
        while len(sent) > limit:                # pathological unbroken run
            take = limit - len(cur) - 1 if cur else limit
            if take < 40:                       # no room left in this chunk
                parts.append(cur)
                cur = ""
                limit = next(gen)
                continue
            head, sent = sent[:take], sent[take:]
            cur = f"{cur} {head}".strip()
            parts.append(cur)
            cur = ""
            limit = next(gen)
        if cur and len(cur) + len(sent) + 1 > limit:
            parts.append(cur)
            cur = sent
            limit = next(gen)
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts

# ------------------------------------------------------------------- audio plumbing


def fix_riff_sizes(b: bytes) -> bytes:
    """Repair streaming WAV headers whose size fields are 0xFFFFFFFF.

    Speechify (and some streaming encoders) emit RIFF/data chunk sizes as the
    placeholder 0xFFFFFFFF; players that trust the header then misread the
    file — heard as a burst of static. Sizes are recomputed from actual length.
    """
    if len(b) < 44 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        return b
    out = bytearray(b)
    struct.pack_into("<I", out, 4, len(out) - 8)
    off = 12
    while off + 8 <= len(out):
        cid = bytes(out[off:off + 4])
        size = struct.unpack_from("<I", out, off + 4)[0]
        actual_rest = len(out) - off - 8
        if cid == b"data":
            if size == 0xFFFFFFFF or size > actual_rest:
                struct.pack_into("<I", out, off + 4, actual_rest)
            break
        if size == 0xFFFFFFFF or size > actual_rest:
            break
        off += 8 + size + (size & 1)
    return bytes(out)


def wrap_pcm(pcm: bytes, out: pathlib.Path, rate: int = 22050) -> None:
    """Wrap raw 16-bit mono PCM in a WAV container (ElevenLabs pcm_* output)."""
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def _which(*names: str) -> str | None:
    from shutil import which
    for n in names:
        if which(n):
            return n
    return None


# Windows gives every console app it spawns a console window of its own, so
# each detached run, each powershell player and the kokoro runner would flash a
# black box on screen. CREATE_NO_WINDOW suppresses it — and is *ignored* when
# combined with DETACHED_PROCESS, so it must replace that flag, not join it.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def quiet_win() -> dict:
    """Popen kwargs that keep Windows from flashing a console window."""
    return {"creationflags": CREATE_NO_WINDOW} if IS_WIN else {}


def player_cmd(wav: pathlib.Path) -> list[str]:
    # mpv first, everywhere: its IPC socket is what makes pause/seek possible.
    if _which("mpv"):
        return ["mpv", "--really-quiet", "--no-video",
                f"--input-ipc-server={MPV_SOCKET}", str(wav)]
    if IS_MAC:
        return ["afplay", str(wav)]
    if IS_WIN:
        path = str(wav).replace("'", "''")
        return ["powershell", "-NoProfile", "-Command",
                f"(New-Object Media.SoundPlayer '{path}').PlaySync()"]
    p = _which("aplay", "paplay", "ffplay")
    if not p:
        sys.exit("no audio player found (need aplay, paplay, or ffplay)")
    if p == "ffplay":
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(wav)]
    return [p, "-q", str(wav)] if p == "aplay" else [p, str(wav)]

# ---------------------------------------------------------------------- providers


def http_json(url: str, payload: dict, headers: dict) -> bytes:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def make_synth(cfg: dict):
    """Return synth(text, out_path) writing a playable WAV, or None when the
    provider speaks directly (system voices, self-playing custom commands)."""
    provider = cfg["provider"]

    if provider == "speechify":
        import base64
        key = api_key(cfg, "speechify")

        def synth(text: str, out: pathlib.Path) -> None:
            raw = http_json(
                "https://api.sws.speechify.com/v1/audio/speech",
                {"input": text, "voice_id": cfg["voice"] or "oliver",
                 "audio_format": "wav"},
                {"Authorization": f"Bearer {key}"})
            audio = base64.b64decode(json.loads(raw)["audio_data"])
            out.write_bytes(fix_riff_sizes(audio))
        return synth

    if provider == "elevenlabs":
        key = api_key(cfg, "elevenlabs")
        voice = cfg["voice"] or "21m00Tcm4TlvDq8ikWAM"      # Rachel (premade)

        def synth(text: str, out: pathlib.Path) -> None:
            pcm = http_json(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
                "?output_format=pcm_22050",
                {"text": text},
                {"xi-api-key": key})
            wrap_pcm(pcm, out)
        return synth

    if provider == "openai":
        key = api_key(cfg, "openai")

        def synth(text: str, out: pathlib.Path) -> None:
            audio = http_json(
                "https://api.openai.com/v1/audio/speech",
                {"model": cfg["tts_model"] or "tts-1",
                 "voice": cfg["voice"] or "alloy",
                 "input": text, "response_format": "wav",
                 "speed": cfg["speed"] or 1.0},
                {"Authorization": f"Bearer {key}"})
            out.write_bytes(fix_riff_sizes(audio))
        return synth

    if provider == "kokoro":
        vp, model, voices_bin, runner = kokoro_paths()
        if not (vp.exists() and model.exists() and voices_bin.exists() and runner.exists()):
            sys.exit("kokoro is not set up — install it via the read-aloud "
                     "plugin: /read-aloud:voice-setup")

        def synth(text: str, out: pathlib.Path) -> None:
            # One long-lived runner keeps the 300MB model loaded across chunks;
            # spawning per chunk would reload it every time.
            global _kokoro_runner
            if _kokoro_runner is None or _kokoro_runner.poll() is not None:
                _kokoro_runner = subprocess.Popen(
                    [str(vp), str(runner), str(model), str(voices_bin)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                    **quiet_win())
            v = cfg["voice"] or "am_michael"
            req = {"text": text, "voice": v, "lang": kokoro_lang(v),
                   "speed": float(cfg["speed"] or 1.0), "out": str(out)}
            _kokoro_runner.stdin.write(json.dumps(req) + "\n")
            _kokoro_runner.stdin.flush()
            line = _kokoro_runner.stdout.readline()
            if not line.startswith("ok"):
                raise RuntimeError(f"kokoro runner: {line.strip() or 'died'}")
        return synth

    if provider == "command":
        template = cfg["command"]
        if not template:
            sys.exit(f'provider "command" needs a command template in {config_path()}')
        if "{out}" not in template:
            return None                          # self-playing command

        def synth(text: str, out: pathlib.Path) -> None:
            cmd = [a.replace("{text}", text).replace("{out}", str(out))
                   for a in shlex.split(template)]
            subprocess.run(cmd, capture_output=True, check=True)
        return synth

    return None                                  # system voices speak directly


def speak_direct(text: str, cfg: dict) -> None:
    """System TTS (and self-playing custom commands): no files, near-zero latency."""
    speed = float(cfg["speed"] or 1.0)
    if cfg["provider"] == "command":
        cmd = [a.replace("{text}", text) for a in shlex.split(cfg["command"])]
    elif IS_MAC:
        cmd = ["say"]
        if cfg["voice"]:
            cmd += ["-v", cfg["voice"]]
        if abs(speed - 1.0) > 0.01:
            cmd += ["-r", str(int(190 * speed))]
        cmd.append(text)
    elif IS_WIN:
        esc = text.replace("'", "''")
        rate = max(-10, min(10, int((speed - 1.0) * 10)))
        sel = (f"$s.SelectVoice('{cfg['voice']}');" if cfg["voice"] else "")
        cmd = ["powershell", "-NoProfile", "-Command",
               "Add-Type -AssemblyName System.Speech;"
               "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
               f"{sel}$s.Rate = {rate};$s.Speak('{esc}')"]
    else:
        if not _which("spd-say"):
            sys.exit("no system voice found — install speech-dispatcher, "
                     "or pick a cloud provider (see --status)")
        cmd = ["spd-say", "-w"]
        if cfg["voice"]:
            cmd += ["-y", cfg["voice"]]
        if abs(speed - 1.0) > 0.01:
            cmd += ["-r", str(max(-100, min(100, int((speed - 1.0) * 100))))]
        cmd.append(text)

    global _player
    claim()
    write_status(phase="speaking", done=0, total=len(text))
    _player = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, **quiet_win())
    _player.wait()
    release()

# ------------------------------------------------------------------ kokoro (local)

_kokoro_runner: subprocess.Popen | None = None


def kokoro_paths() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path]:
    """Kokoro's install is read-aloud's: model, venv, and runner live in its
    data dir — explain-aloud reuses them and never installs anything."""
    d = data_dir()
    vp = d / "venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    return vp, d / "kokoro" / "kokoro-v1.0.onnx", d / "kokoro" / "voices-v1.0.bin", \
        d / "kokoro_runner.py"

# ----------------------------------------------------------------------- playback


_player: subprocess.Popen | None = None


def stop() -> None:
    """Stop whoever holds the reading. Never this process — see claim()."""
    if not PIDFILE.exists():
        return
    try:
        pid = int(PIDFILE.read_text().strip())
    except (OSError, ValueError):
        PIDFILE.unlink(missing_ok=True)
        return
    if pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass                                     # already gone, or not ours
    PIDFILE.unlink(missing_ok=True)


def claim() -> None:
    """Become the one reader: stop the current reading, then take the pidfile.

    Claimed when a run STARTS, not when its audio does. A reading spends its
    first seconds loading a model or waiting on an API, and a second click
    landing in that window used to find an empty pidfile, stop nothing, and
    leave two voices talking over each other.
    """
    stop()
    PIDFILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, _on_term)
    _sweep_parts()
    try:                                  # a dead reading's socket is stale
        MPV_SOCKET.unlink(missing_ok=True)
    except OSError:
        pass


def release() -> None:
    """Hand back the pidfile if it is still ours, and take our audio with us."""
    try:
        if PIDFILE.read_text().strip() == str(os.getpid()):
            PIDFILE.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
    for part in PARTS:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
    clear_status()


def _on_term(_sig=None, _frm=None):
    if _player and _player.poll() is None:
        _player.terminate()
    if _kokoro_runner and _kokoro_runner.poll() is None:
        _kokoro_runner.terminate()
    release()
    _clear_worker_pidfile()
    os._exit(0)


def _clear_worker_pidfile() -> None:
    try:
        worker_pidfile().unlink(missing_ok=True)
    except OSError:
        pass


def _sweep_parts() -> None:
    """Bin scratch audio a crashed reading left behind — an hour is long past."""
    cutoff = time.time() - 3600
    try:
        for part in pathlib.Path(tempfile.gettempdir()).glob(PART_GLOB):
            if part.stat().st_mtime < cutoff:
                part.unlink(missing_ok=True)
    except OSError:
        pass


def play_chunked(text: str, cfg: dict, synth) -> None:
    """Gapless chunked playback: synthesise chunk i+1 while chunk i plays."""
    global _player
    claim()                                      # never overlap two readings

    chunks = chunk_text(text, int(cfg["first_chars"]), int(cfg["chunk_chars"]))
    total = sum(len(c) for c in chunks)
    synth(chunks[0], PARTS[0])
    for i in range(len(chunks)):
        st = _speaking_status(chunks, i)
        write_status(**st)
        cur = PARTS[i % 2]
        _player = subprocess.Popen(
            player_cmd(cur), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **quiet_win())
        stop_evt = threading.Event()
        threading.Thread(target=_chunk_progress_loop,
                         args=(st["done"], len(chunks[i]), total, stop_evt),
                         daemon=True).start()
        prefetch = None
        if i + 1 < len(chunks):
            prefetch = threading.Thread(
                target=synth, args=(chunks[i + 1], PARTS[(i + 1) % 2]), daemon=True)
            prefetch.start()
        _player.wait()
        stop_evt.set()
        if prefetch:
            prefetch.join()
    release()

# --------------------------------------------------------------------------- modes


def detach(argv: list[str]) -> None:
    """Re-launch this script detached so the caller returns immediately."""
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL}
    if IS_WIN:
        kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, os.path.abspath(__file__), *argv], **kwargs)


def speak_text(text: str, cfg: dict) -> None:
    """Speak plain text with the configured engine (claims at speak time)."""
    synth = make_synth(cfg)
    if synth is None:
        speak_direct(text, cfg)
    else:
        play_chunked(text, cfg, synth)


def hook_mode() -> int:
    """Stop-hook entry: exit fast and NEVER fail — Claude's flow comes first."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if os.environ.get("EXPLAIN_ALOUD_CHILD"):
            return 0                     # our own claude -p run; never recurse
        cfg = load_cfg()
        if cfg.get("mode") == "off":
            return 0
        if cfg.get("ra_auto_read"):
            print("explain-aloud: read-aloud's auto_read is on — skipping so two "
                  "voices never race. Turn it off with /read-aloud:speak-auto.",
                  file=sys.stderr)
            return 0
        path = event.get("transcript_path", "")
        if path and pathlib.Path(path).exists():
            detach(["--worker", "--transcript", path])
    except Exception as e:                # noqa: BLE001 — deliberate
        print(f"explain-aloud hook: {e}", file=sys.stderr)
    return 0


def worker_mode(transcript: str, force: bool = False) -> int:
    """Detached pipeline: extract → trigger → generate → speak.

    The worker pidfile is written BEFORE generation and claim() happens only
    at speak time — a new prompt cancels a stale worker via the pidfile, so a
    slow generation can never claim over a newer reply's explanation.
    `force` (the explicit /explain command) bypasses the mode gate; only an
    empty reply still stays silent.
    """
    try:
        worker_pidfile().write_text(str(os.getpid()))
    except OSError:
        pass
    signal.signal(signal.SIGTERM, _on_term)
    try:
        cfg = load_cfg()
        user_msg, reply = last_exchange(pathlib.Path(transcript),
                                        int(cfg["context_cap_chars"]))
        if force:
            if not reply.strip():
                return 0
        elif not should_speak(reply, cfg):
            return 0
        write_status(phase="generating", started=time.time())
        history = recent_exchanges(pathlib.Path(transcript),
                                   int(cfg.get("context_messages", 6)),
                                   int(cfg.get("history_cap_chars", 3000)))
        script = generate_script(user_msg, reply, cfg, history)
        speak_text(script, cfg)
        return 0
    finally:
        _clear_worker_pidfile()
        clear_status()


def ipc_payload(action: str, seconds: float = 10) -> dict:
    """One mpv IPC command: pause, resume, or seek back within the chunk."""
    if action == "pause":
        return {"command": ["set_property", "pause", True]}
    if action == "resume":
        return {"command": ["set_property", "pause", False]}
    return {"command": ["seek", -abs(seconds), "relative"]}


def control_mode(action: str, seconds: float = 10) -> int:
    """Send one control command to the playing mpv. Always exits 0."""
    if not MPV_SOCKET.exists():
        print("explain-aloud: nothing is playing (pause/resume/back need the "
              "mpv player — install mpv if it is missing)")
        return 0
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(MPV_SOCKET))
            s.sendall((json.dumps(ipc_payload(action, seconds)) + "\n").encode())
    except OSError as e:
        print(f"explain-aloud: control failed: {e}", file=sys.stderr)
    return 0


def cancel_mode() -> int:
    """UserPromptSubmit entry: a new prompt makes pending audio stale."""
    try:
        if os.environ.get("EXPLAIN_ALOUD_CHILD"):
            return 0    # our own claude -p child must not cancel its parent
        for f in WORKER_PID_DIR.glob(WORKER_PID_GLOB):
            try:
                pid = int(f.read_text().strip())
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            f.unlink(missing_ok=True)
        stop()
        clear_status()
    except Exception:                     # noqa: BLE001 — deliberate
        pass
    return 0

# A voice's first letter encodes its language, and phonemization must follow the
# voice: a Spanish voice fed English phonemes reads Spanish with an English
# accent. Each code below was verified against a voice of that language.
#
# Every entry carries a display label, American English included: three voices
# share the name "santa" and two each share "dora"/"alex"/"alpha" across
# languages, so an unlabelled default would read as ambiguous, not merely terse.
KOKORO_LANGS = {
    "a": ("en-us", "American"),  "b": ("en-gb", "British"),
    "e": ("es", "Spanish"),      "f": ("fr-fr", "French"),
    "h": ("hi", "Hindi"),        "i": ("it", "Italian"),
    "j": ("ja", "Japanese"),     "p": ("pt-br", "Brazilian Portuguese"),
    "z": ("cmn", "Mandarin"),
}


def kokoro_lang(voice: str) -> str:
    """espeak language code for a Kokoro voice id, by its prefix letter."""
    return KOKORO_LANGS.get((voice or "a")[:1], ("en-us", ""))[0]


def show_status(cfg: dict) -> int:
    print(f"config    : {config_path()}")
    print(f"mode      : {cfg['mode']}")
    print(f"model     : {cfg['model']}  (timeout {cfg['timeout_s']}s, "
          f"≤{cfg['max_speech_words']} words)")
    print(f"auto      : >{cfg['auto_min_chars']} chars, "
          f"≥{cfg['auto_min_bullets']} bullets, or table/code present")
    print(f"voice     : {cfg['provider']}"
          + (f" / {cfg['voice']}" if cfg["voice"] else " (default)")
          + f" @ {cfg['speed']}  (from {ra_config_path()})")
    print(f"control   : {'mpv available — pause/resume/back work' if _which('mpv') else 'mpv NOT installed — no pause/resume/back'}")
    if cfg.get("ra_auto_read"):
        print("WARNING   : read-aloud's auto_read is ON — explain-aloud skips "
              "speaking while it is, so two voices never race. Turn it off "
              "with /read-aloud:speak-auto.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--text")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--transcript")
    ap.add_argument("--project")
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--hook", action="store_true")
    ap.add_argument("--cancel", action="store_true")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="with --worker: speak even when mode/thresholds say not to")
    ap.add_argument("--pause", action="store_true",
                    help="pause the explanation being spoken")
    ap.add_argument("--resume", action="store_true",
                    help="resume a paused explanation")
    ap.add_argument("--back", nargs="?", const=5.0, type=float, default=None,
                    metavar="SECONDS",
                    help="seek back (default 5s) within the current audio "
                         "chunk; rewinding across chunk boundaries is not "
                         "supported")
    ap.add_argument("--mode", choices=["always", "auto", "off"],
                    help="when explanations speak; persisted")
    ap.add_argument("--set-model", dest="set_model", metavar="ALIAS",
                    help="claude CLI model alias for script generation "
                         "(haiku, sonnet, opus); persisted")
    ap.add_argument("--set-context", dest="set_context", type=int, metavar="N",
                    help="how many earlier thread messages go to the prompt "
                         "as context (0 = none); persisted")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="print the generated spoken script, speak nothing")
    args = ap.parse_args()

    if args.hook:
        return hook_mode()
    if args.cancel:
        return cancel_mode()
    if args.pause:
        return control_mode("pause")
    if args.resume:
        return control_mode("resume")
    if args.back is not None:
        return control_mode("back", args.back)
    if args.stop:
        stop()
        return 0
    if args.mode:
        save_cfg({"mode": args.mode})
        print(f"mode set to {args.mode}")
        return 0
    if args.set_model:
        save_cfg({"model": args.set_model})
        print(f"model set to {args.set_model}")
        return 0
    if args.set_context is not None:
        n = max(0, args.set_context)
        save_cfg({"context_messages": n})
        print(f"context set to {n} earlier messages")
        return 0

    cfg = load_cfg()

    if args.status:
        return show_status(cfg)

    if args.print_only:
        t = (pathlib.Path(args.transcript) if args.transcript
             else newest_transcript(args.project))
        if not t or not t.exists():
            print("no transcript found", file=sys.stderr)
            return 1
        user_msg, reply = last_exchange(t, int(cfg["context_cap_chars"]))
        print(generate_script(user_msg, reply, cfg))
        return 0

    if args.detach:
        argv = [a for a in sys.argv[1:] if a != "--detach"]
        detach(argv)
        print("generating the spoken explanation (~15–40s), audio follows…")
        return 0

    if args.worker:
        t = (pathlib.Path(args.transcript) if args.transcript
             else newest_transcript(args.project))
        if not t or not t.exists():
            return 0
        return worker_mode(str(t), force=args.force)

    if args.stdin:
        raw = sys.stdin.read()
    elif args.text:
        raw = args.text
    else:
        t = (pathlib.Path(args.transcript) if args.transcript
             else newest_transcript(args.project))
        if not t or not t.exists():
            print("no transcript found", file=sys.stderr)
            return 1
        raw = last_reply(t)

    text = spoken_form(raw, int(cfg["max_chars"]))
    if not text:
        print("nothing to speak", file=sys.stderr)
        return 1

    # Claimed before the engine is built, not after: loading a 300MB model or
    # waiting on a cloud voice is exactly the window a second click lands in.
    claim()
    speak_text(text, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
