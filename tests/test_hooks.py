import io
import json
import pathlib
import subprocess
import sys

import explain

SCRIPT = pathlib.Path(explain.__file__)


def _cfg(**over):
    cfg = dict(explain.RA_DEFAULTS)
    cfg.update(explain.EA_DEFAULTS)
    cfg.update(over)
    return cfg


def _feed_stdin(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))


def _record_detach(monkeypatch):
    calls = []
    monkeypatch.setattr(explain, "detach", lambda argv: calls.append(argv))
    return calls


def test_hook_exits_zero_on_garbage_stdin(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--hook"],
        input="not json", capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}, timeout=15)
    assert r.returncode == 0


def test_hook_skips_when_sentinel_env_set(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text("{}")
    monkeypatch.setenv("EXPLAIN_ALOUD_CHILD", "1")
    monkeypatch.setattr(explain, "load_cfg", _cfg)
    calls = _record_detach(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"transcript_path": str(t)}))
    assert explain.hook_mode() == 0
    assert calls == []


def test_hook_skips_when_mode_off(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text("{}")
    monkeypatch.delenv("EXPLAIN_ALOUD_CHILD", raising=False)
    monkeypatch.setattr(explain, "load_cfg", lambda: _cfg(mode="off"))
    calls = _record_detach(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"transcript_path": str(t)}))
    assert explain.hook_mode() == 0
    assert calls == []


def test_hook_skips_and_warns_when_read_aloud_auto_read_on(tmp_path, monkeypatch, capsys):
    t = tmp_path / "s.jsonl"
    t.write_text("{}")
    monkeypatch.delenv("EXPLAIN_ALOUD_CHILD", raising=False)
    monkeypatch.setattr(explain, "load_cfg",
                        lambda: _cfg(mode="always", ra_auto_read=True))
    calls = _record_detach(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"transcript_path": str(t)}))
    assert explain.hook_mode() == 0
    assert calls == []
    assert "auto_read" in capsys.readouterr().err


def test_hook_detaches_worker_with_transcript_path(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text("{}")
    monkeypatch.delenv("EXPLAIN_ALOUD_CHILD", raising=False)
    monkeypatch.setattr(explain, "load_cfg", lambda: _cfg(mode="always"))
    calls = _record_detach(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"transcript_path": str(t)}))
    assert explain.hook_mode() == 0
    assert calls == [["--worker", "--transcript", str(t)]]


def test_cancel_kills_all_worker_pidfiles_and_playback(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPLAIN_ALOUD_CHILD", raising=False)
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    (tmp_path / "claude-explain-aloud-worker-54321.pid").write_text("54321")
    (tmp_path / "claude-explain-aloud-worker-54322.pid").write_text("54322")
    killed = []
    monkeypatch.setattr(explain.os, "kill", lambda pid, sig: killed.append(pid))
    stopped = []
    monkeypatch.setattr(explain, "stop", lambda: stopped.append(True))
    assert explain.cancel_mode() == 0
    assert sorted(killed) == [54321, 54322]
    assert stopped == [True]
    assert not list(tmp_path.glob("claude-explain-aloud-worker-*.pid"))


def test_cancel_skips_when_sentinel_env_set(tmp_path, monkeypatch):
    # The worker's own `claude -p` child loads these hooks too; its
    # UserPromptSubmit must NOT cancel the very worker waiting on it.
    monkeypatch.setenv("EXPLAIN_ALOUD_CHILD", "1")
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    (tmp_path / "claude-explain-aloud-worker-54321.pid").write_text("54321")
    killed = []
    monkeypatch.setattr(explain.os, "kill", lambda pid, sig: killed.append(pid))
    stopped = []
    monkeypatch.setattr(explain, "stop", lambda: stopped.append(True))
    assert explain.cancel_mode() == 0
    assert killed == [] and stopped == []
    assert (tmp_path / "claude-explain-aloud-worker-54321.pid").exists()


def _worker_fixture(tmp_path, monkeypatch, reply="the answer"):
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": reply}]}}))
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    spoken = []
    monkeypatch.setattr(explain, "generate_script",
                        lambda user_msg, reply, cfg, history="": "spoken script")
    monkeypatch.setattr(explain, "speak_text", lambda text, cfg: spoken.append(text))
    return t, spoken


def test_worker_writes_own_pid_file_before_generation(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": "the answer"}]}}))
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    monkeypatch.setattr(explain, "load_cfg", lambda: _cfg(mode="always"))
    pidfile = tmp_path / f"claude-explain-aloud-worker-{explain.os.getpid()}.pid"
    seen = {}

    def fake_generate(user_msg, reply, cfg, history=""):
        seen["pidfile_during_generation"] = pidfile.exists()
        return "spoken script"

    spoken = []
    monkeypatch.setattr(explain, "generate_script", fake_generate)
    monkeypatch.setattr(explain, "speak_text", lambda text, cfg: spoken.append(text))
    assert explain.worker_mode(str(t)) == 0
    assert seen["pidfile_during_generation"] is True
    assert spoken == ["spoken script"]
    assert not pidfile.exists()          # cleaned up on the way out


def test_worker_force_overrides_mode_off(tmp_path, monkeypatch):
    t, spoken = _worker_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(explain, "load_cfg", lambda: _cfg(mode="off"))
    assert explain.worker_mode(str(t)) == 0
    assert spoken == []                  # gated as usual without force
    assert explain.worker_mode(str(t), force=True) == 0
    assert spoken == ["spoken script"]   # explicit request speaks anyway


def test_worker_force_still_skips_empty_reply(tmp_path, monkeypatch):
    t, spoken = _worker_fixture(tmp_path, monkeypatch, reply="   ")
    monkeypatch.setattr(explain, "load_cfg", _cfg)
    assert explain.worker_mode(str(t), force=True) == 0
    assert spoken == []
