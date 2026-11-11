import subprocess

import explain


def _cfg(**over):
    cfg = {"model": "haiku", "timeout_s": 60, "max_speech_words": 250}
    cfg.update(over)
    return cfg


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_invokes_claude_cli_with_model_and_sentinel(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw.get("env")
        seen["input"] = kw.get("input")
        return _Result(stdout="The fix is simple.")

    monkeypatch.setattr(explain.subprocess, "run", fake_run)
    out = explain.generate_script("why?", "because.", _cfg())
    assert out == "The fix is simple."
    assert seen["argv"][:4] == ["claude", "-p", "--model", "haiku"]
    assert "--strict-mcp-config" in seen["argv"]   # no MCP servers: cheap + fast
    assert seen["env"]["EXPLAIN_ALOUD_CHILD"] == "1"
    assert "because." in seen["input"]


def test_timeout_returns_fallback_line(monkeypatch):
    def fake_run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

    monkeypatch.setattr(explain.subprocess, "run", fake_run)
    assert explain.generate_script("q", "r", _cfg()) == explain.FALLBACK_LINE


def test_missing_cli_returns_fallback_line(monkeypatch):
    def fake_run(argv, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(explain.subprocess, "run", fake_run)
    assert explain.generate_script("q", "r", _cfg()) == explain.FALLBACK_LINE


def test_empty_output_returns_fallback_line(monkeypatch):
    monkeypatch.setattr(explain.subprocess, "run",
                        lambda argv, **kw: _Result(stdout="  \n"))
    assert explain.generate_script("q", "r", _cfg()) == explain.FALLBACK_LINE


def test_nonzero_exit_returns_fallback_line(monkeypatch):
    monkeypatch.setattr(explain.subprocess, "run",
                        lambda argv, **kw: _Result(stdout="oops", returncode=1))
    assert explain.generate_script("q", "r", _cfg()) == explain.FALLBACK_LINE


def test_sanitize_strips_fences_timestamp_and_markdown():
    assert explain.sanitize_script("[12:34]\n```\nHello there\n```") == "Hello there"
    assert explain.sanitize_script("**Bold** and `code` stay words") == "Bold and code stay words"
    assert explain.sanitize_script("- first point\n- second point") == "first point second point"


def test_prompt_contains_reply_user_msg_and_word_budget():
    p = explain.build_prompt("my question", "my reply", _cfg(max_speech_words=123))
    assert "my question" in p and "my reply" in p
    assert "at most 123 words" in p
    assert "teacher" in p
