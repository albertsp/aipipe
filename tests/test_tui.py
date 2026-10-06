import subprocess
import sys
from pathlib import Path

import pytest

from aipipe import config as cfgmod
from aipipe import tui


def test_build_menu_returns_expected_actions():
    menu = tui.build_menu()
    labels = [m[0] for m in menu]
    assert labels == [
        "init",
        "install-agents",
        "doctor",
        "sandbox-check",
        "budget",
        "route (vista previa)",
        "run --dry-run",
        "watch",
        "salir",
    ]
    # route es la única acción que necesita texto del usuario.
    route = next(m for m in menu if m[0] == "route (vista previa)")
    assert route[2] is not None
    assert "{file}" in route[1]


def test_main_selects_doctor_and_runs_subprocess(monkeypatch, tmp_path, capsys):
    # Proyecto temporal con .git y configuración mínima (antes de mockear subprocess).
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".aipipe.toml").write_text('[linear]\nteam = "ENG"\n')
    monkeypatch.chdir(tmp_path)

    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tui.subprocess, "run", fake_run)
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", True)

    idx = [m[0] for m in tui.build_menu()].index("doctor")
    assert tui.main(_select=idx) == 0
    assert calls == [[sys.executable, "-m", "aipipe", "doctor"]]


def test_main_without_prompt_toolkit_shows_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", False)
    code = tui.main()
    err = capsys.readouterr().err
    assert code == 2
    assert "prompt-toolkit" in err
    assert "aipipe[tui]" in err


def test_run_action_uses_temp_file_for_route_input(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tui.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)

    code = tui.run_action(["route", "--file", "{file}"], extra="# Título\nDescripción")
    assert code == 0
    assert len(calls) == 1
    assert calls[0][:3] == [sys.executable, "-m", "aipipe"]
    assert calls[0][3:5] == ["route", "--file"]
    temp_file = Path(calls[0][5])
    assert temp_file.exists() is False  # se borra tras ejecutar
    # No deja rastro en tmp_path.
    assert list(tmp_path.iterdir()) == []


def test_cli_tui_subcommand_exists():
    from aipipe import cli

    parser = cli.build_parser()
    args = parser.parse_args(["tui"])
    assert args.cmd == "tui"
    assert args.fn is cli.cmd_tui


prompt_toolkit = pytest.importorskip("prompt_toolkit", reason="prompt-toolkit no instalado")


def test_application_style_is_a_prompt_toolkit_style(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".aipipe.toml").write_text('[linear]\nteam = "ENG"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", True)

    captured = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return None

    monkeypatch.setattr(tui, "Application", FakeApp)

    tui.main()
    assert "style" in captured
    assert isinstance(captured["style"], prompt_toolkit.styles.Style)
    assert not isinstance(captured["style"], dict)


def test_input_flow_runs_route_with_temp_file(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / ".aipipe.toml").write_text('[linear]\nteam = "ENG"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", True)

    route_idx = [m[0] for m in tui.build_menu()].index("route (vista previa)")

    class FakeApp:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return ("input", route_idx)

    monkeypatch.setattr(tui, "Application", FakeApp)
    monkeypatch.setattr(tui, "_ask_input", lambda prompt, title: "Texto de prueba")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tui.subprocess, "run", fake_run)

    code = tui.main()
    assert code == 0
    assert len(calls) == 1
    assert calls[0][:3] == [sys.executable, "-m", "aipipe"]
    assert calls[0][3:5] == ["route", "--file"]
    temp_file = Path(calls[0][5])
    assert not temp_file.exists()
