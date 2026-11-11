import json
import pathlib

import explain


def _user(text):
    return json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": text}]}})


def _user_plain(text):
    return json.dumps({"type": "user", "message": {"content": text}})


def _assistant(*blocks):
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _text(t):
    return {"type": "text", "text": t}


def _write(tmp_path, lines) -> pathlib.Path:
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_returns_last_user_msg_and_last_reply(tmp_path):
    p = _write(tmp_path, [
        _user("old question"),
        _assistant(_text("old answer")),
        _user("new question"),
        _assistant(_text("new answer")),
    ])
    assert explain.last_exchange(p, 4000) == ("new question", "new answer")


def test_plain_string_user_content(tmp_path):
    p = _write(tmp_path, [_user_plain("plain question"), _assistant(_text("answer"))])
    assert explain.last_exchange(p, 4000) == ("plain question", "answer")


def test_user_msg_truncated_to_cap(tmp_path):
    p = _write(tmp_path, [_user("x" * 50), _assistant(_text("answer"))])
    user_msg, reply = explain.last_exchange(p, 10)
    assert user_msg == "x" * 10 and reply == "answer"


def test_skips_malformed_and_non_json_lines(tmp_path):
    p = _write(tmp_path, [
        "not json at all",
        _user("question"),
        '{"type": "assistant", "message": "weird shape"}',
        _assistant(_text("answer")),
        "{broken",
    ])
    assert explain.last_exchange(p, 4000) == ("question", "answer")


def test_tool_only_reply_yields_empty_reply(tmp_path):
    p = _write(tmp_path, [
        _user("question"),
        _assistant({"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}),
    ])
    assert explain.last_exchange(p, 4000) == ("question", "")


def test_missing_file_yields_empty_pair(tmp_path):
    assert explain.last_exchange(tmp_path / "nope.jsonl", 4000) == ("", "")
