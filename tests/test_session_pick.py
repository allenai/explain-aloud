"""The Explain button/skill must speak the pressing session's reply, not the
newest transcript across sessions (the cross-session bug)."""
import argparse
import pathlib
import time

import explain


def _mk(projects, proj, sid, mtime_bump=0):
    d = projects / proj
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text("{}\n")
    if mtime_bump:
        t = time.time() + mtime_bump
        import os
        os.utime(p, (t, t))
    return p


def test_session_transcript_finds_by_id(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "PROJECTS", tmp_path)
    mine = _mk(tmp_path, "-home-u-proj-a", "aaaa-1111")
    _mk(tmp_path, "-home-u-proj-b", "bbbb-2222", mtime_bump=100)  # newer, other session
    assert explain.session_transcript("aaaa-1111") == mine


def test_session_transcript_missing_id_none(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "PROJECTS", tmp_path)
    assert explain.session_transcript("zzzz") is None
    assert explain.session_transcript("") is None
    assert explain.session_transcript("../../etc/passwd") is None


def _args(**over):
    base = dict(transcript=None, session_id=None, project=None)
    base.update(over)
    return argparse.Namespace(**base)


def test_pick_transcript_prefers_session_id_over_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "PROJECTS", tmp_path)
    mine = _mk(tmp_path, "-proj-a", "aaaa-1111")
    _mk(tmp_path, "-proj-a", "cccc-3333", mtime_bump=100)  # newer sibling session
    picked = explain.pick_transcript(_args(session_id="aaaa-1111", project="/proj/a"))
    assert picked == mine


def test_pick_transcript_explicit_path_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "PROJECTS", tmp_path)
    mine = _mk(tmp_path, "-proj-a", "aaaa-1111")
    picked = explain.pick_transcript(
        _args(transcript=str(mine), session_id="other-id"))
    assert picked == mine


def test_pick_transcript_falls_back_to_project_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "PROJECTS", tmp_path)
    newest = _mk(tmp_path, "-proj-a", "cccc-3333", mtime_bump=100)
    picked = explain.pick_transcript(_args(session_id="gone-id", project="proj/a"))
    assert picked == newest
