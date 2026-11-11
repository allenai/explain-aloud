import json

import explain


def _user(text):
    return json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": text}]}})


def _assistant(text):
    return json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": text}]}})


def _write(tmp_path, lines):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_recent_exchanges_excludes_final_pair_and_limits_k(tmp_path):
    p = _write(tmp_path, [
        _user("q1"), _assistant("a1"),
        _user("q2"), _assistant("a2"),
        _user("q3"), _assistant("a3"),   # final pair: not history
    ])
    h = explain.recent_exchanges(p, k=2, cap=4000)
    assert h == "User: q2\nAssistant: a2"
    assert "q3" not in h and "a3" not in h


def test_recent_exchanges_caps_total_chars(tmp_path):
    p = _write(tmp_path, [
        _user("x" * 300), _assistant("y" * 300),
        _user("final q"), _assistant("final a"),
    ])
    h = explain.recent_exchanges(p, k=4, cap=100)
    assert len(h) <= 100


def test_recent_exchanges_zero_k_is_empty(tmp_path):
    p = _write(tmp_path, [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2")])
    assert explain.recent_exchanges(p, k=0, cap=4000) == ""


def test_prompt_includes_history_block_only_when_given():
    cfg = {"max_speech_words": 250}
    with_h = explain.build_prompt("q", "r", cfg, history="User: earlier q\nAssistant: earlier a")
    assert "earlier q" in with_h and "context only" in with_h
    without = explain.build_prompt("q", "r", cfg)
    assert "context only" not in without
