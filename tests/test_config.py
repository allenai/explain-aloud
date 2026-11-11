import json

import explain


def _point_configs(monkeypatch, tmp_path):
    monkeypatch.setenv("EXPLAIN_ALOUD_CONFIG", str(tmp_path / "explain-aloud.json"))
    monkeypatch.setenv("READ_ALOUD_CONFIG", str(tmp_path / "read-aloud.json"))


def test_defaults_when_no_config_files(tmp_path, monkeypatch):
    _point_configs(monkeypatch, tmp_path)
    cfg = explain.load_cfg()
    assert cfg["mode"] == "always" and cfg["model"] == "haiku"
    assert cfg["timeout_s"] == 60 and cfg["context_cap_chars"] == 4000
    assert cfg["max_speech_words"] == 250
    assert cfg["auto_min_chars"] == 700 and cfg["auto_min_bullets"] == 6


def test_voice_comes_from_read_aloud_config(tmp_path, monkeypatch):
    _point_configs(monkeypatch, tmp_path)
    (tmp_path / "read-aloud.json").write_text(
        json.dumps({"provider": "kokoro", "voice": "am_onyx", "speed": 0.8}))
    cfg = explain.load_cfg()
    assert (cfg["provider"], cfg["voice"], cfg["speed"]) == ("kokoro", "am_onyx", 0.8)


def test_corrupt_read_aloud_config_falls_back(tmp_path, monkeypatch):
    _point_configs(monkeypatch, tmp_path)
    (tmp_path / "read-aloud.json").write_text("{not json")
    cfg = explain.load_cfg()
    assert cfg["provider"] == "system"  # read-aloud's own default


def test_own_config_overrides_defaults(tmp_path, monkeypatch):
    _point_configs(monkeypatch, tmp_path)
    (tmp_path / "explain-aloud.json").write_text(json.dumps({"mode": "auto", "timeout_s": 30}))
    cfg = explain.load_cfg()
    assert cfg["mode"] == "auto" and cfg["timeout_s"] == 30
