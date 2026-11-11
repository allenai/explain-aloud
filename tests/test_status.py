import json

import explain


def test_speaking_status_percent_basis():
    chunks = ["aaaa", "bbbbbb"]
    assert explain._speaking_status(chunks, 0) == {"phase": "speaking", "done": 0, "total": 10}
    assert explain._speaking_status(chunks, 1) == {"phase": "speaking", "done": 4, "total": 10}


def test_interp_done_clamps_and_scales():
    # halfway through a 100-char chunk that follows 200 spoken chars
    assert explain._interp_done(200, 100, pos=5, dur=10) == 250
    assert explain._interp_done(200, 100, pos=0, dur=10) == 200
    assert explain._interp_done(200, 100, pos=99, dur=10) == 300   # clamped
    assert explain._interp_done(200, 100, pos=5, dur=0) == 200     # no duration yet


def test_worker_writes_then_clears_status(tmp_path, monkeypatch):
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": "the answer"}]}}))
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    status = tmp_path / "status.json"
    monkeypatch.setattr(explain, "STATUS_FILE", status)
    monkeypatch.setattr(explain, "load_cfg",
                        lambda: {**explain.RA_DEFAULTS, **explain.EA_DEFAULTS,
                                 "mode": "always"})
    seen = {}

    def fake_generate(user_msg, reply, cfg):
        seen["status"] = json.loads(status.read_text())
        return "spoken script"

    monkeypatch.setattr(explain, "generate_script", fake_generate)
    monkeypatch.setattr(explain, "speak_text", lambda text, cfg: None)
    assert explain.worker_mode(str(t)) == 0
    assert seen["status"]["phase"] == "generating" and "started" in seen["status"]
    assert not status.exists()           # cleared on the way out


def test_cancel_clears_status(tmp_path, monkeypatch):
    monkeypatch.delenv("EXPLAIN_ALOUD_CHILD", raising=False)
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    status = tmp_path / "status.json"
    status.write_text("{}")
    monkeypatch.setattr(explain, "STATUS_FILE", status)
    monkeypatch.setattr(explain, "stop", lambda: None)
    assert explain.cancel_mode() == 0
    assert not status.exists()
