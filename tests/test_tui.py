import os
from datetime import datetime, timezone

import pytest

from aipipe import config as cfgmod, tui
from aipipe.ledger import Entry, Ledger
from aipipe.linear import LinearError, LinearTracker
from aipipe.pipeline import WaitState, save_wait


def _cfg(linear_server, **linear_cfg):
    state, url = linear_server
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    cfg["linear"].update(linear_cfg)
    cfg["project"].update(push=False, pr=False)
    return state, cfg


def test_main_without_prompt_toolkit_shows_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", False)
    code = tui.main()
    err = capsys.readouterr().err
    assert code == 2
    assert "prompt-toolkit" in err
    assert "aipipe[tui]" in err


def test_cli_tui_subcommand_exists():
    from aipipe import cli

    parser = cli.build_parser()
    args = parser.parse_args(["tui"])
    assert args.cmd == "tui"
    assert args.fn is cli.cmd_tui


# --- recopilación y render ---------------------------------------------------
def test_collect_snapshot_status_queue_budget(repo, linear_server):
    state, cfg = _cfg(linear_server)
    snap = tui.collect_snapshot(cfg)
    assert [t.identifier for t in snap.queue] == ["ENG-1"]
    assert snap.queue[0].title == "Anadir feature"
    assert snap.root == str(cfg.root)
    assert set(snap.budget) == {"5h", "week", "month"}
    assert snap.soft_stop == cfg["budget"]["soft_stop"]
    assert snap.active is False

    status = "\n".join(tui.render_status(snap))
    assert "servicio:" in status and "cola ai-ready" in status
    assert "ENG-1" in status

    budget = "\n".join(tui.render_budget(snap))
    assert "5h" in budget and "week" in budget and "month" in budget

    queue = "\n".join(tui.render_queue(snap))
    assert "cola ai-ready" in queue and "ENG-1" in queue


def test_current_ticket_shows_phase_tier_cost(repo, linear_server, home):
    state, cfg = _cfg(linear_server)
    # issue en curso, esperando aprobación, con tier y fase y gasto anotado.
    iss = state["issues"][0]
    iss["state"] = {"id": "s-prog", "name": "In Progress", "type": "started"}
    iss["labels"]["nodes"] = [
        {"id": "l-waiting", "name": "ai-waiting"},
        {"id": "l-std", "name": "ai:standard"},
        {"id": "l-phase-impl", "name": "ai-phase-impl"},
    ]
    Ledger().append(
        Entry(
            ts=datetime.now(timezone.utc), issue="ENG-1", role="aipipe-plan", model="m",
            requests=1, tokens={}, cost_usd=0.05, normalized=0.0,
        )
    )
    snap = tui.collect_snapshot(cfg)
    assert snap.current is not None and snap.current.identifier == "ENG-1"
    assert snap.current.waiting is True
    assert snap.current.tier == "standard"
    assert snap.current.phase == "impl"
    assert snap.current.cost_usd == pytest.approx(0.05)

    status = "\n".join(tui.render_status(snap))
    assert "en curso: ENG-1" in status
    assert "esperando aprobación" in status
    assert "fase: impl" in status and "tier: standard" in status
    assert "$0.0500" in status


def test_detail_shows_description_plan_and_comments(repo, linear_server, home):
    state, cfg = _cfg(linear_server)
    tracker = LinearTracker(cfg, log=lambda *_: None)
    save_wait(
        WaitState("ENG-1", "Anadir feature", "standard", "razón", "1. hacer X", "c-1",
                  "2026-01-01T00:00:00.000Z", 0, str(cfg.root))
    )
    state.setdefault("comments", []).append(
        {"id": "c-9", "issueId": "id-ENG-1", "body": "aprobado", "createdAt": "2026-10-05T10:00:09.000Z",
         "user": {"id": "u-me", "name": "Yo", "email": "me@x.com"}}
    )
    tv = tui._view(tracker.get("ENG-1"), cfg, {})
    detail = tui.fetch_detail(cfg, tracker, tv)
    text = "\n".join(tui.render_detail(detail))
    assert "ENG-1" in text
    assert "Crear feature.txt" in text          # descripción
    assert "1. hacer X" in text                 # plan desde el estado de espera
    assert "aprobado" in text                   # comentario reciente


# --- color / NO_COLOR --------------------------------------------------------
def test_no_color_disables_styles(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert tui.use_color() is False
    assert tui._class("title") == ""

    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert tui.use_color() is True
    assert tui._class("title") == "class:title"


# --- carga de clave y sanitización -------------------------------------------
def test_env_loaded_and_secret_never_leaks(repo, home, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    (home / "env").write_text('LINEAR_API_KEY="super-secret-key-123"\n# comentario\nFOO=bar\n')
    loaded = tui.ensure_env()
    assert loaded["LINEAR_API_KEY"] == "super-secret-key-123"
    assert os.environ["LINEAR_API_KEY"] == "super-secret-key-123"

    cfg = cfgmod.load()
    dash = tui.Dashboard(cfg)
    dash.log("la clave es super-secret-key-123 aquí")
    assert "super-secret-key-123" not in dash.log_lines[-1]
    assert "***" in dash.log_lines[-1]


# --- acciones con la misma ruta que el CLI ------------------------------------
def test_launch_uses_cli_cmd_run_same_route(repo, linear_server, monkeypatch):
    state, cfg = _cfg(linear_server)
    dash = tui.Dashboard(cfg)
    dash.refresh()
    assert dash.snapshot and dash.snapshot.queue

    from aipipe import cli

    calls = []

    def fake_cmd_run(args):
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "cmd_run", fake_cmd_run)

    class FakeThread:
        def __init__(self, target=None, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(tui.threading, "Thread", FakeThread)

    dash.launch()
    assert len(calls) == 1
    ns = calls[0]
    assert ns.issue == ["ENG-1"]
    assert ns.dry_run is False and ns.from_file is None and ns.max is None


def test_doctor_and_sandbox_check_delegate_to_cli(repo, linear_server, monkeypatch):
    state, cfg = _cfg(linear_server)
    dash = tui.Dashboard(cfg)
    from aipipe import cli

    called = {"doctor": 0, "sandbox": 0}
    monkeypatch.setattr(cli, "cmd_doctor", lambda args: called.__setitem__("doctor", 1) or 0)
    monkeypatch.setattr(cli, "cmd_sandbox_check", lambda args: called.__setitem__("sandbox", 1) or 0)

    class FakeThread:
        def __init__(self, target=None, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(tui.threading, "Thread", FakeThread)
    dash.doctor()
    dash.sandbox_check()
    assert called == {"doctor": 1, "sandbox": 1}


# --- errores de red/Linear como aviso, no como cierre -------------------------
def test_linear_failure_becomes_warning(monkeypatch, repo):
    cfg = cfgmod.load()

    class BadTracker:
        def __init__(self, *a, **kw):
            raise LinearError("red caída")

    monkeypatch.setattr(tui, "LinearTracker", BadTracker)
    dash = tui.Dashboard(cfg)
    dash.refresh()  # no debe lanzar excepción
    assert "red caída" in dash.warning
    assert dash.snapshot is not None  # snapshot vacío para no romper el render


# --- salir no deja procesos ni muta tickets -----------------------------------
def test_exit_bindings_present_and_actions_are_in_process(repo, linear_server):
    state, cfg = _cfg(linear_server)
    dash = tui.Dashboard(cfg)
    kb = tui._bindings(dash)
    keys = {tuple(b.keys) for b in kb.bindings}
    assert ("q",) in keys
    assert ("c-c",) in keys

    # Las acciones se ejecutan en hilos daemon (sin subprocess): no quedan procesos huérfanos.
    assert "subprocess" not in vars(tui)


# --- render estable ante cambios de tamaño ------------------------------------
def test_render_stable_across_widths(repo, linear_server):
    state, cfg = _cfg(linear_server)
    snap = tui.collect_snapshot(cfg)
    tracker = LinearTracker(cfg, log=lambda *_: None)
    detail = tui.fetch_detail(cfg, tracker, snap.queue[0])
    for width in (40, 80, 120):
        text = tui.render_snapshot(snap, detail, [], width=width)
        assert "ENG-1" in text
        assert "consumo de Go" in text
    assert tui.render_snapshot(snap, detail, [], width=80) == tui.render_snapshot(snap, detail, [], width=80)


# --- integración con prompt_toolkit (opcional) --------------------------------
prompt_toolkit = pytest.importorskip("prompt_toolkit", reason="prompt-toolkit no instalado")


def test_main_builds_application_with_layout_and_style(monkeypatch, repo):
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    captured = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return None

    monkeypatch.setattr(tui, "Application", FakeApp)

    assert tui.main() == 0
    assert "layout" in captured and "key_bindings" in captured
    assert captured["full_screen"] is True
    assert isinstance(captured["style"], prompt_toolkit.styles.Style)


def test_main_omits_style_when_no_color(monkeypatch, repo):
    monkeypatch.setattr(tui, "_HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setenv("NO_COLOR", "1")
    captured = {}

    class FakeApp:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return None

    monkeypatch.setattr(tui, "Application", FakeApp)

    assert tui.main() == 0
    assert captured["style"] is None
