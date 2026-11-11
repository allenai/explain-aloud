import json
import pathlib
import sys

import pytest

import explain

from test_config import _run_cli


# ------------------------------------------------------------------ prompt

def _cfg(**over):
    cfg = {**explain.RA_DEFAULTS, **explain.EA_DEFAULTS}
    cfg.update(over)
    return cfg


def test_default_prompt_has_no_language_block():
    p = explain.build_prompt("q", "r", _cfg())
    assert "ENTIRE spoken script" not in p
    assert "spoken English" in p


def test_hebrew_language_adds_block():
    p = explain.build_prompt("q", "r", _cfg(language="hebrew"))
    assert "ENTIRE spoken script in Hebrew" in p


def test_english_aliases_add_no_block():
    for lang in ("english", "English", "en", ""):
        p = explain.build_prompt("q", "r", _cfg(language=lang))
        assert "ENTIRE spoken script" not in p


# ------------------------------------------------------------------ CLI

def test_set_language_persists_and_status_shows_it(tmp_path):
    r = _run_cli(tmp_path, "--set-language", "hebrew")
    assert r.returncode == 0
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert saved["language"] == "hebrew"
    s = _run_cli(tmp_path, "--status")
    assert "hebrew" in s.stdout.lower()


def test_set_language_english_resets(tmp_path):
    _run_cli(tmp_path, "--set-language", "hebrew")
    r = _run_cli(tmp_path, "--set-language", "english")
    assert r.returncode == 0
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert saved["language"] == "english"


# -------------------------------------------- auto provider switch on set-language

def test_set_language_auto_picks_elevenlabs_when_keyed(tmp_path):
    (tmp_path / "read-aloud.json").write_text(json.dumps(
        {"provider": "kokoro", "voice": "af_heart",
         "api_keys": {"elevenlabs": "k", "openai": "k"}}))
    r = _run_cli(tmp_path, "--set-language", "hebrew")
    assert r.returncode == 0 and "elevenlabs" in r.stdout
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert saved["provider"] == "elevenlabs" and saved["voice"]


def test_set_language_falls_back_to_openai_key(tmp_path):
    (tmp_path / "read-aloud.json").write_text(json.dumps(
        {"provider": "kokoro", "api_keys": {"openai": "k"}}))
    _run_cli(tmp_path, "--set-language", "hebrew")
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert saved["provider"] == "openai"


def test_set_language_no_key_warns_keeps_provider(tmp_path):
    (tmp_path / "read-aloud.json").write_text(json.dumps({"provider": "kokoro"}))
    r = _run_cli(tmp_path, "--set-language", "hebrew")
    assert "key" in r.stdout.lower()
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert not saved.get("provider")


def test_set_language_keeps_capable_provider(tmp_path):
    (tmp_path / "read-aloud.json").write_text(json.dumps(
        {"provider": "openai", "voice": "nova",
         "api_keys": {"elevenlabs": "k"}}))
    _run_cli(tmp_path, "--set-language", "hebrew")
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert not saved.get("provider")   # openai already speaks hebrew


def test_set_language_english_clears_override(tmp_path):
    (tmp_path / "read-aloud.json").write_text(json.dumps(
        {"provider": "kokoro", "api_keys": {"elevenlabs": "k"}}))
    _run_cli(tmp_path, "--set-language", "hebrew")
    _run_cli(tmp_path, "--set-language", "english")
    saved = json.loads((tmp_path / "explain-aloud.json").read_text())
    assert not saved.get("provider") and not saved.get("voice")


def test_own_provider_override_wins_over_read_aloud(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_ALOUD_CONFIG", str(tmp_path / "explain-aloud.json"))
    monkeypatch.setenv("READ_ALOUD_CONFIG", str(tmp_path / "read-aloud.json"))
    (tmp_path / "read-aloud.json").write_text(json.dumps(
        {"provider": "kokoro", "voice": "af_heart"}))
    (tmp_path / "explain-aloud.json").write_text(json.dumps(
        {"provider": "elevenlabs", "voice": "V1"}))
    cfg = explain.load_cfg()
    assert (cfg["provider"], cfg["voice"]) == ("elevenlabs", "V1")


# ------------------------------------------------------------------ elevenlabs model

def _elevenlabs_payload(monkeypatch, cfg):
    captured = {}

    def fake_http_json(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        return b"\x00\x00"  # fake pcm

    monkeypatch.setattr(explain, "http_json", fake_http_json)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    synth = explain.make_synth(cfg)
    synth("shalom", pathlib.Path("/tmp/el-test-out.wav"))
    return captured


def test_elevenlabs_non_english_defaults_to_v3(tmp_path, monkeypatch):
    cfg = _cfg(provider="elevenlabs", language="hebrew")
    cap = _elevenlabs_payload(monkeypatch, cfg)
    assert cap["payload"]["model_id"] == "eleven_v3"


def test_elevenlabs_english_sends_no_model(tmp_path, monkeypatch):
    cfg = _cfg(provider="elevenlabs")
    cap = _elevenlabs_payload(monkeypatch, cfg)
    assert "model_id" not in cap["payload"]


def test_elevenlabs_tts_model_wins(tmp_path, monkeypatch):
    cfg = _cfg(provider="elevenlabs", language="hebrew",
               tts_model="eleven_multilingual_v2")
    cap = _elevenlabs_payload(monkeypatch, cfg)
    assert cap["payload"]["model_id"] == "eleven_multilingual_v2"
