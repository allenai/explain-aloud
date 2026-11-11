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
    monkeypatch.setattr(explain, "load_cfg", lambda: _cfg(ra_auto_read=True))
    calls = _record_detach(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"transcript_path": str(t)}))
    assert explain.hook_mode() == 0
    assert calls == []
    assert "auto_read" in capsys.readouterr().err


def test_hook_detaches_worker_with_transcript_path(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text("{}")
    monkeypatch.delenv("EXPLAIN_ALOUD_CHILD", raising=False)
    monkeypatch.setattr(explain, "load_cfg", _cfg)
    calls = _record_detach(monkeypatch)
    _feed_stdin(monkeypatch, json.dumps({"transcript_path": str(t)}))
    assert explain.hook_mode() == 0
    assert calls == [["--worker", "--transcript", str(t)]]


def test_cancel_kills_worker_pidfile_and_playback(tmp_path, monkeypatch):
    pidfile = tmp_path / "worker.pid"
    pidfile.write_text("54321")
    monkeypatch.setattr(explain, "WORKER_PIDFILE", pidfile)
    killed = []
    monkeypatch.setattr(explain.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    stopped = []
    monkeypatch.setattr(explain, "stop", lambda: stopped.append(True))
    assert explain.cancel_mode() == 0
    assert killed and killed[0][0] == 54321
    assert stopped == [True]
    assert not pidfile.exists()


def test_worker_writes_pidfile_before_generation(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": "the answer"}]}}))
    pidfile = tmp_path / "worker.pid"
    monkeypatch.setattr(explain, "WORKER_PIDFILE", pidfile)
    monkeypatch.setattr(explain, "load_cfg", _cfg)
    seen = {}

    def fake_generate(user_msg, reply, cfg):
        seen["pidfile_during_generation"] = pidfile.exists()
        return "spoken script"

    spoken = []
    monkeypatch.setattr(explain, "generate_script", fake_generate)
    monkeypatch.setattr(explain, "speak_text", lambda text, cfg: spoken.append(text))
    assert explain.worker_mode(str(t)) == 0
    assert seen["pidfile_during_generation"] is True
    assert spoken == ["spoken script"]
    assert not pidfile.exists()          # cleaned up on the way out
