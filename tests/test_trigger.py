import explain


def _cfg(**over):
    cfg = {"mode": "always", "auto_min_chars": 700, "auto_min_bullets": 6}
    cfg.update(over)
    return cfg


def test_mode_off_never_speaks():
    assert explain.should_speak("a long interesting reply " * 100, _cfg(mode="off")) is False


def test_mode_always_speaks_nonempty_and_skips_empty():
    assert explain.should_speak("short answer", _cfg()) is True
    assert explain.should_speak("", _cfg()) is False
    assert explain.should_speak("   \n ", _cfg()) is False


def test_auto_short_prose_silent():
    assert explain.should_speak("a" * 200, _cfg(mode="auto")) is False


def test_auto_long_prose_speaks():
    assert explain.should_speak("word " * 200, _cfg(mode="auto")) is True


def test_auto_table_or_code_fence_speaks():
    assert explain.should_speak("see:\n| a | b |\n| 1 | 2 |", _cfg(mode="auto")) is True
    assert explain.should_speak("run\n```\nls\n```", _cfg(mode="auto")) is True


def test_auto_many_bullets_speaks():
    bullets = "\n".join(f"- item {i}" for i in range(6))
    assert explain.should_speak(bullets, _cfg(mode="auto")) is True
    few = "\n".join(f"- item {i}" for i in range(3))
    assert explain.should_speak(few, _cfg(mode="auto")) is False
