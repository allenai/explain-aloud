"""Re-explaining the same message reuses the generated script and audio."""
import os
import pathlib

import pytest

import explain


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setenv("EXPLAIN_ALOUD_CACHE", str(d))
    return d


def _cfg(**over):
    cfg = {**explain.RA_DEFAULTS, **explain.EA_DEFAULTS}
    cfg.update(over)
    return cfg


# ------------------------------------------------------------------ script

def test_script_generated_once_then_reused(cache, monkeypatch):
    calls = []

    def fake_gen(user_msg, reply, cfg, history=""):
        calls.append(reply)
        return "spoken words"

    monkeypatch.setattr(explain, "generate_script", fake_gen)
    cfg = _cfg()
    a = explain.cached_script("q", "the reply", cfg, "hist")
    b = explain.cached_script("q", "the reply", cfg, "hist")
    assert a == b == "spoken words"
    assert len(calls) == 1


def test_fallback_line_never_cached(cache, monkeypatch):
    calls = []

    def fake_gen(user_msg, reply, cfg, history=""):
        calls.append(1)
        return explain.FALLBACK_LINE

    monkeypatch.setattr(explain, "generate_script", fake_gen)
    explain.cached_script("q", "r", _cfg())
    explain.cached_script("q", "r", _cfg())
    assert len(calls) == 2


def test_script_cache_misses_on_language_or_model_change(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(explain, "generate_script",
                        lambda u, r, c, h="": calls.append(1) or "x")
    explain.cached_script("q", "r", _cfg())
    explain.cached_script("q", "r", _cfg(language="hebrew"))
    explain.cached_script("q", "r", _cfg(model="sonnet"))
    explain.cached_script("q", "other reply", _cfg())
    assert len(calls) == 4


def test_cache_disabled_always_generates(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(explain, "generate_script",
                        lambda u, r, c, h="": calls.append(1) or "x")
    explain.cached_script("q", "r", _cfg(cache=False))
    explain.cached_script("q", "r", _cfg(cache=False))
    assert len(calls) == 2


# ------------------------------------------------------------------ audio

def _counting_synth(calls):
    def synth(text, out):
        calls.append(text)
        pathlib.Path(out).write_bytes(b"RIFF" + text.encode())
    return synth


def test_audio_chunk_synthesised_once_then_reused(cache, tmp_path):
    calls = []
    synth = explain.caching_synth(_counting_synth(calls), _cfg(provider="kokoro"))
    out1, out2 = tmp_path / "a.wav", tmp_path / "b.wav"
    synth("hello there", out1)
    synth("hello there", out2)
    assert calls == ["hello there"]
    assert out1.read_bytes() == out2.read_bytes() == b"RIFFhello there"


def test_audio_cache_misses_on_voice_change(cache, tmp_path):
    calls = []
    base = _counting_synth(calls)
    explain.caching_synth(base, _cfg(voice="af_heart"))("hi", tmp_path / "a.wav")
    explain.caching_synth(base, _cfg(voice="am_onyx"))("hi", tmp_path / "b.wav")
    assert len(calls) == 2


def test_failed_synth_not_cached(cache, tmp_path):
    def boom(text, out):
        raise RuntimeError("tts down")

    synth = explain.caching_synth(boom, _cfg())
    with pytest.raises(RuntimeError):
        synth("hi", tmp_path / "a.wav")
    assert not any(cache.rglob("*.wav")) if cache.exists() else True


# ------------------------------------------------------------------ pruning

def test_prune_drops_oldest_beyond_cap(cache):
    cache.mkdir(parents=True)
    for i in range(5):
        p = cache / f"f{i}.wav"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (1000 + i, 1000 + i))      # f0 oldest
    explain.prune_cache(max_bytes=2500)
    left = sorted(p.name for p in cache.iterdir())
    assert left == ["f3.wav", "f4.wav"]
