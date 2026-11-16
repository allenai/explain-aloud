"""Count-back: explain a reply N exchanges before the last, and never treat
explain-aloud's own slash command (or injected/meta entries) as an exchange."""
import json
import pathlib

import explain


def _u(text, **extra):
    return json.dumps({"type": "user", "message": {"content": text}, **extra})


def _a(text):
    return json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": text}]}})


def _a_tool():
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}})


def _w(tmp_path, lines) -> pathlib.Path:
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


CONVO = [
    _u("q1"), _a("BIG interesting answer"),
    _u("q2"), _a("ran it, done"),
    _u("q3"), _a("tiny ack"),
]


def test_nth_one_is_last(tmp_path):
    assert explain.last_exchange(_w(tmp_path, CONVO), 4000, nth=1) == ("q3", "tiny ack")


def test_nth_counts_back(tmp_path):
    p = _w(tmp_path, CONVO)
    assert explain.last_exchange(p, 4000, nth=2) == ("q2", "ran it, done")
    assert explain.last_exchange(p, 4000, nth=3) == ("q1", "BIG interesting answer")


def test_nth_beyond_start_clamps_to_oldest(tmp_path):
    p = _w(tmp_path, CONVO)
    assert explain.last_exchange(p, 4000, nth=99) == ("q1", "BIG interesting answer")


def test_history_ends_before_the_chosen_exchange(tmp_path):
    p = _w(tmp_path, CONVO)
    h = explain.recent_exchanges(p, k=10, cap=4000, nth=2)
    assert h == "User: q1\nAssistant: BIG interesting answer"
    assert explain.recent_exchanges(p, k=10, cap=4000, nth=3) == ""


def test_own_slash_command_is_not_an_exchange(tmp_path):
    lines = CONVO + [
        _u("<command-message>explain-aloud:explain</command-message>\n"
           "<command-name>/explain-aloud:explain</command-name>\n"
           "<command-args>2</command-args>"),
        _u("Base directory for this skill: /x\n\nRun exactly this…", isMeta=True),
        _a_tool(),
        _a("🎙️ explaining aloud — generating the spoken script…"),
    ]
    p = _w(tmp_path, lines)
    assert explain.last_exchange(p, 4000) == ("q3", "tiny ack")
    assert explain.last_exchange(p, 4000, nth=2) == ("q2", "ran it, done")


def test_local_commands_do_not_hide_the_last_reply(tmp_path):
    lines = CONVO + [
        _u("<local-command-caveat>Caveat: …</local-command-caveat>", isMeta=True),
        _u("<command-name>/model</command-name>\n<command-args>opus</command-args>"),
        _u("<local-command-stdout>Set model to opus</local-command-stdout>"),
    ]
    p = _w(tmp_path, lines)
    assert explain.last_exchange(p, 4000) == ("q3", "tiny ack")


def test_cli_nth_flag_reaches_worker(tmp_path, monkeypatch):
    p = _w(tmp_path, CONVO)
    seen = {}

    def fake_worker(transcript, force=False, nth=1):
        seen.update(transcript=transcript, force=force, nth=nth)
        return 0

    monkeypatch.setattr(explain, "worker_mode", fake_worker)
    monkeypatch.setattr("sys.argv", ["explain.py", "--worker", "--force",
                                     "--transcript", str(p), "--nth", "3"])
    assert explain.main() == 0
    assert seen == {"transcript": str(p), "force": True, "nth": 3}


def test_worker_explains_chosen_exchange(tmp_path, monkeypatch):
    p = _w(tmp_path, CONVO)
    monkeypatch.setattr(explain, "WORKER_PID_DIR", tmp_path)
    monkeypatch.setattr(explain, "STATUS_FILE", tmp_path / "st.json")
    monkeypatch.setattr(explain, "load_cfg",
                        lambda: {**explain.RA_DEFAULTS, **explain.EA_DEFAULTS})
    got = {}

    def fake_gen(user_msg, reply, cfg, history=""):
        got.update(user=user_msg, reply=reply, history=history)
        return "spoken"

    monkeypatch.setattr(explain, "generate_script", fake_gen)
    monkeypatch.setattr(explain, "speak_text", lambda t, c: None)
    assert explain.worker_mode(str(p), force=True, nth=3) == 0
    assert got == {"user": "q1", "reply": "BIG interesting answer", "history": ""}


def test_count_prints_number_of_exchanges(tmp_path, monkeypatch, capsys):
    p = _w(tmp_path, CONVO + [
        _u("<command-name>/explain-aloud:explain</command-name>"),
        _a("🎙️ explaining aloud…"),
    ])
    monkeypatch.setattr("sys.argv", ["explain.py", "--count", "--transcript", str(p)])
    assert explain.main() == 0
    assert capsys.readouterr().out.strip() == "3"


def test_count_missing_transcript_prints_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["explain.py", "--count",
                                     "--transcript", str(tmp_path / "nope.jsonl")])
    assert explain.main() == 0
    assert capsys.readouterr().out.strip() == "0"
