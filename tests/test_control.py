import pathlib

import explain


def test_ipc_payloads():
    assert explain.ipc_payload("pause") == {"command": ["set_property", "pause", True]}
    assert explain.ipc_payload("resume") == {"command": ["set_property", "pause", False]}
    assert explain.ipc_payload("back") == {"command": ["seek", -10, "relative"]}
    assert explain.ipc_payload("back", 5) == {"command": ["seek", -5, "relative"]}


def test_player_cmd_prefers_mpv_with_ipc_socket(monkeypatch):
    monkeypatch.setattr(explain, "_which", lambda *names: "mpv" if "mpv" in names else None)
    cmd = explain.player_cmd(pathlib.Path("/tmp/x.wav"))
    assert cmd[0] == "mpv"
    assert "--no-video" in cmd
    assert any(a.startswith("--input-ipc-server=") for a in cmd)
    assert cmd[-1] == "/tmp/x.wav"


def test_player_cmd_falls_back_without_mpv(monkeypatch):
    monkeypatch.setattr(explain, "_which",
                        lambda *names: None if "mpv" in names else "aplay")
    cmd = explain.player_cmd(pathlib.Path("/tmp/x.wav"))
    assert cmd[0] == "aplay"


def test_control_without_socket_prints_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(explain, "MPV_SOCKET", tmp_path / "missing.sock")
    assert explain.control_mode("pause") == 0
    assert "mpv" in capsys.readouterr().out
