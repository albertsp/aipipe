"""ALB-27: los agentes y los tests no deben poder leer la clave de Linear ni el token de GitHub."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from aipipe import config as cfgmod, sandbox

SECRET = "lin_api_SECRETO_NO_FILTRAR"
GH = "ghp_SECRETO_GITHUB"

needs_bwrap = pytest.mark.skipif(
    not sandbox.probe(cfgmod.load())[0], reason="bwrap no disponible (sudo apt install bubblewrap)"
)


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """Un HOME falso con los archivos sensibles del runner y las claves en el entorno."""
    home = tmp_path / "home"
    (home / ".config" / "aipipe").mkdir(parents=True)
    (home / ".config" / "aipipe" / "env").write_text(f"LINEAR_API_KEY={SECRET}\n")
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text(f"oauth_token: {GH}\n")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("CLAVE-PRIVADA\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LINEAR_API_KEY", SECRET)
    monkeypatch.setenv("GH_TOKEN", GH)
    monkeypatch.setenv("GITHUB_TOKEN", GH)
    monkeypatch.setenv("MI_SECRETO_CUALQUIERA", "algo-que-no-esta-en-la-lista")
    return home


@pytest.fixture
def worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


def cfg_for(sandbox_mode: str):
    cfg = cfgmod.load()
    cfg["sandbox"]["mode"] = sandbox_mode
    return cfg


# ------------------------------------------------------------------ entorno por lista blanca
def test_env_whitelist_drops_secrets(secrets, monkeypatch):
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    monkeypatch.setenv("OPENCODE_DISABLE_AUTOUPDATE", "1")
    env = sandbox.build_env(cfg_for("off"))
    assert "LINEAR_API_KEY" not in env and "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert "MI_SECRETO_CUALQUIERA" not in env          # lo que no esta en la lista no pasa
    assert env["LANG"] == "es_ES.UTF-8" and env["OPENCODE_DISABLE_AUTOUPDATE"] == "1" and "PATH" in env and "HOME" in env
    assert SECRET not in "".join(env.values()) and GH not in "".join(env.values())


def test_env_never_passes_forbidden_even_if_allowed(secrets):
    cfg = cfg_for("off")
    cfg["sandbox"]["env_allow_extra"] = ["LINEAR_API_KEY", "GH_*", "GITHUB_TOKEN", "*"]
    env = sandbox.build_env(cfg)
    assert "LINEAR_API_KEY" not in env and "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env


def test_env_forbids_custom_api_key_env_name(secrets, monkeypatch):
    monkeypatch.setenv("MI_CLAVE_LINEAR", "otra")
    cfg = cfg_for("off")
    cfg["linear"]["api_key_env"] = "MI_CLAVE_LINEAR"
    cfg["sandbox"]["env_allow_extra"] = ["MI_CLAVE_LINEAR"]
    assert "MI_CLAVE_LINEAR" not in sandbox.build_env(cfg)


def test_extra_allow_adds_variables(secrets, monkeypatch):
    monkeypatch.setenv("MI_VAR", "x")
    cfg = cfg_for("off")
    cfg["sandbox"]["env_allow_extra"] = ["MI_VAR"]
    assert sandbox.build_env(cfg)["MI_VAR"] == "x"


# ------------------------------------------------------------------ modos
def test_mode_off_returns_command_unchanged(secrets, worktree):
    assert sandbox.wrap(cfg_for("off"), ["echo", "hi"], worktree) == ["echo", "hi"]


def test_invalid_mode_is_rejected(monkeypatch):
    monkeypatch.delenv("AIPIPE_SANDBOX")
    with pytest.raises(sandbox.SandboxError):
        sandbox.requested_mode(cfg_for("quizas"))


def test_mode_bwrap_without_bwrap_is_an_error_not_silent(monkeypatch, worktree):
    cfg = cfg_for("bwrap")
    cfg["sandbox"]["bwrap_bin"] = "bwrap-que-no-existe"
    monkeypatch.delenv("AIPIPE_SANDBOX")
    with pytest.raises(sandbox.SandboxError):
        sandbox.wrap(cfg, ["echo"], worktree)
    state, text = sandbox.describe(cfg)
    assert state is False and "no esta" in text


def test_mode_auto_without_bwrap_degrades_with_warning(monkeypatch, worktree):
    cfg = cfg_for("auto")
    cfg["sandbox"]["bwrap_bin"] = "bwrap-que-no-existe"
    monkeypatch.delenv("AIPIPE_SANDBOX")
    assert sandbox.effective_mode(cfg) == "off"
    state, text = sandbox.describe(cfg)
    assert state is None and text.startswith("AVISO")


def test_doctor_warns_when_sandbox_off(secrets):
    state, text = sandbox.describe(cfg_for("off"))
    assert state is None and "AVISO" in text and "clave de Linear" in text


# ------------------------------------------------------------------ prueba negativa con bwrap de verdad
def run_in(cfg, script, cwd, env=None, network=True):
    argv = sandbox.wrap(cfg, ["/bin/sh", "-c", script], cwd, network=network)
    return subprocess.run(argv, cwd=cwd, env=env or sandbox.build_env(cfg), capture_output=True, text=True, timeout=60)


@needs_bwrap
def test_agent_cannot_read_linear_key_by_any_route(secrets, worktree, monkeypatch):
    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    cfg = cfg_for("bwrap")
    script = (
        'env; echo ---; cat /proc/$PPID/environ 2>&1 | tr "\\0" "\\n"; echo ---; '
        'for p in /proc/[0-9]*; do cat $p/environ 2>/dev/null | tr "\\0" "\\n"; done; echo ---; '
        'cat "$HOME/.config/aipipe/env" 2>&1; cat "$HOME/.config/gh/hosts.yml" 2>&1; cat "$HOME/.ssh/id_ed25519" 2>&1; '
        'ls -A "$HOME"; ls /proc | grep -c "^[0-9]"'
    )
    out = run_in(cfg, script, worktree)
    text = out.stdout + out.stderr
    assert out.returncode == 0, text
    for secreto in (SECRET, GH, "CLAVE-PRIVADA", "algo-que-no-esta-en-la-lista"):
        assert secreto not in text, f"se filtro {secreto!r}:\n{text}"


@needs_bwrap
def test_runner_process_is_invisible_inside_the_sandbox(secrets, worktree, monkeypatch):
    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    out = run_in(cfg_for("bwrap"), f'test -d /proc/{os.getpid()} && echo VISIBLE || echo oculto', worktree)
    assert "oculto" in out.stdout


@needs_bwrap
def test_control_without_sandbox_the_same_script_does_leak(secrets, worktree, monkeypatch):
    """Control positivo: sin sandbox y con el entorno completo, la fuga existe. Si esto no filtrase, la prueba negativa
    no demostraria nada."""
    monkeypatch.setenv("AIPIPE_SANDBOX", "off")
    out = subprocess.run(["/bin/sh", "-c", 'cat "$HOME/.config/aipipe/env"; env'], cwd=worktree, env=os.environ.copy(),
                         capture_output=True, text=True)
    assert SECRET in out.stdout


@needs_bwrap
def test_worktree_is_writable_and_home_is_not_persisted(secrets, worktree, monkeypatch):
    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    out = run_in(cfg_for("bwrap"), 'echo hecho > feature.txt && echo x > "$HOME/escape.txt"; echo x > /usr/escape 2>&1; true', worktree)
    assert (worktree / "feature.txt").read_text() == "hecho\n"          # el agente puede trabajar en el worktree
    assert not (secrets / "escape.txt").exists()                        # lo escrito en el HOME no sale del sandbox
    assert "Read-only" in out.stdout + out.stderr or "denied" in out.stdout + out.stderr.lower()


@needs_bwrap
def test_git_metadata_is_read_only_inside_the_sandbox(secrets, tmp_path, monkeypatch):
    """Si el agente pudiera escribir en .git, podria dejar un hook que el runner ejecutaria (fuera del sandbox)."""
    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    cfg = cfg_for("bwrap")
    out = run_in(cfg, 'mkdir -p .git/hooks && echo "#!/bin/sh" > .git/hooks/pre-commit; echo x > .git/config2; echo ok > nuevo.txt; true', repo)
    assert not (repo / ".git" / "hooks" / "pre-commit").exists() and not (repo / ".git" / "config2").exists()
    assert (repo / "nuevo.txt").exists()


@needs_bwrap
def test_tests_run_without_network_when_configured(secrets, worktree, monkeypatch):
    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    cfg = cfg_for("bwrap")
    out = run_in(cfg, f'{sys.executable} -c "import socket; socket.create_connection((\'1.1.1.1\', 53), timeout=3)"', worktree, network=False)
    assert out.returncode != 0


@needs_bwrap
def test_killing_the_wrapper_kills_everything_inside(secrets, worktree, monkeypatch):
    """La cancelacion mata a bwrap; --die-with-parent y el espacio de PIDs propio se llevan al resto."""
    import signal
    import time

    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    cfg = cfg_for("bwrap")
    marker = f"aipipe-sleep-{os.getpid()}"
    argv = sandbox.wrap(cfg, ["/bin/bash", "-c", f"sleep 300 & exec -a {marker} sleep 301"], worktree)
    proc = subprocess.Popen(argv, cwd=worktree, env=sandbox.build_env(cfg), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    alive = lambda: subprocess.run(["pgrep", "-f", f"^({marker}|sleep 300$)"], capture_output=True, text=True).stdout.strip()
    assert alive() != ""                                  # control: antes de matar, los procesos existen
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=10)
    deadline = time.time() + 5
    while alive() and time.time() < deadline:             # el nucleo los termina de forma asincrona
        time.sleep(0.1)
    assert alive() == ""


@needs_bwrap
def test_worktree_git_metadata_read_only_but_git_can_read(secrets, tmp_path, monkeypatch):
    """El caso real: worktree con `.git` de archivo. El agente puede leer (git status/diff) pero no escribir en el
    metadato: ni el archivo `.git` del worktree, ni `.git/worktrees/<id>`, ni los hooks del repositorio principal."""
    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    main = tmp_path / "proj"
    main.mkdir()
    g = lambda *a, cwd=main: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (main / "a.txt").write_text("1\n")
    g("add", "-A")
    g("commit", "-qm", "init")
    wt = tmp_path / "wt-alb1"
    g("worktree", "add", "-q", "-b", "ai/alb-1", str(wt))
    monkeypatch.chdir(main)
    cfg = cfg_for("bwrap")
    assert str(cfg.root) == str(main.resolve()) or cfg.root.resolve() == main.resolve()
    gitfile_before = (wt / ".git").read_text()
    out = run_in(
        cfg,
        'echo "gitdir: /otro" > .git 2>/dev/null; echo x > "$(sed s/gitdir:.//  .git)/commondir" 2>/dev/null; '
        f'echo "#!/bin/sh" > {main}/.git/hooks/pre-commit 2>/dev/null; echo cambio >> a.txt; git status --short; git diff --stat',
        wt,
    )
    assert (wt / ".git").read_text() == gitfile_before
    assert not (main / ".git" / "hooks" / "pre-commit").exists()
    assert not (main / ".git" / "worktrees" / "wt-alb1" / "commondir").read_text().startswith("x")
    assert "a.txt" in out.stdout                                         # git funciona dentro (lectura) y ve el cambio
    assert (wt / "a.txt").read_text() == "1\ncambio\n"                   # y el agente edita el worktree con normalidad


@needs_bwrap
def test_sandbox_check_command_passes(secrets, tmp_path, monkeypatch, capsys):
    from aipipe import cli

    monkeypatch.setenv("AIPIPE_SANDBOX", "bwrap")
    monkeypatch.setenv("AIPIPE_HOME", str(tmp_path / "ah"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["sandbox-check"]) == 0
    out = capsys.readouterr().out
    assert "ERR" not in out and "Sandbox correcto" in out


def test_sandbox_check_fails_loudly_when_off(secrets, tmp_path, monkeypatch, capsys):
    from aipipe import cli

    monkeypatch.setenv("AIPIPE_SANDBOX", "off")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["sandbox-check"]) == 1
    assert "NO uses aipipe" in capsys.readouterr().out
