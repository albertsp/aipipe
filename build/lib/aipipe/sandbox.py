"""Aislamiento de lo que ejecuta codigo no confiable: los agentes de OpenCode y los tests del proyecto.

Dos capas (ALB-27):

1. **Entorno por lista blanca.** Al proceso hijo solo llegan las variables permitidas; nunca LINEAR_API_KEY ni tokens
   de GitHub, aunque esten en el entorno del runner.
2. **Sandbox con bubblewrap (`bwrap`).** Un proceso del mismo usuario puede leer el entorno de su padre
   (`/proc/<pid>/environ`) y los archivos del usuario (`~/.config/aipipe/env`, `~/.config/gh`, `~/.ssh`), asi que
   limpiar el entorno no basta. Dentro del sandbox:
   - el HOME esta vacio (tmpfs) salvo lo imprescindible: OpenCode, el venv, el worktree;
   - `/proc` es propio (espacio de PIDs nuevo): los procesos del runner no existen para el agente;
   - el resto del sistema es de solo lectura; `/tmp` es privado;
   - el metadato de git del proyecto es de solo lectura (un agente que escribiera ahi podria hacer que el runner
     ejecute codigo suyo al hacer commit);
   - sin capacidades ni `setuid` (`sudo` no funciona).

La red NO se restringe aqui (OpenCode la necesita): ver ALB-31.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

# Nunca llegan a un hijo, ni aunque se pidan en env_allow / env_allow_extra.
ALWAYS_DENY = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "LINEAR_API_KEY")

_probe_cache: dict[str, tuple[bool, str]] = {}


class SandboxError(RuntimeError):
    pass


# ------------------------------------------------------------------ entorno
def build_env(cfg: dict, extra: dict | None = None) -> dict:
    """Entorno minimo para un hijo: solo las variables permitidas, menos las prohibidas."""
    sb = cfg["sandbox"]
    patterns = list(sb["env_allow"]) + list(sb.get("env_allow_extra", []))
    deny = {d.upper() for d in ALWAYS_DENY} | {str(cfg["linear"]["api_key_env"]).upper()}
    env = {}
    for name, value in os.environ.items():
        if name.upper() in deny:
            continue
        if any(fnmatch.fnmatchcase(name, p) for p in patterns):
            env[name] = value
    env.update(extra or {})
    return env


# ------------------------------------------------------------------ bwrap
def requested_mode(cfg: dict) -> str:
    mode = os.environ.get("AIPIPE_SANDBOX") or str(cfg["sandbox"]["mode"])
    mode = mode.strip().lower()
    if mode not in ("auto", "bwrap", "off"):
        raise SandboxError(f"sandbox.mode = '{mode}' no es valido (auto|bwrap|off)")
    return mode


def probe(cfg: dict) -> tuple[bool, str]:
    """¿Se puede usar bwrap en esta maquina? (instalado y con espacios de nombres de usuario permitidos)."""
    binary = cfg["sandbox"]["bwrap_bin"]
    if binary in _probe_cache:
        return _probe_cache[binary]
    path = shutil.which(binary)
    if not path:
        res = (False, f"'{binary}' no esta instalado (sudo apt install bubblewrap)")
    else:
        try:
            proc = subprocess.run(
                [path, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid", "--unshare-ipc",
                 "--cap-drop", "ALL", "true"],
                capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", "")},
            )
            err = (proc.stderr or proc.stdout).strip()
            res = (True, f"{path}") if proc.returncode == 0 else (False, f"{path} no puede crear el sandbox: {err[:300]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            res = (False, f"{path} fallo al probarse: {exc}")
    _probe_cache[binary] = res
    return res


def effective_mode(cfg: dict) -> str:
    """'bwrap' u 'off'. Con mode=bwrap y sin bwrap utilizable, es un error (no se degrada en silencio)."""
    mode = requested_mode(cfg)
    if mode == "off":
        return "off"
    ok, why = probe(cfg)
    if ok:
        return "bwrap"
    if mode == "bwrap":
        raise SandboxError(f"sandbox.mode = 'bwrap' pero no esta disponible: {why}")
    return "off"


def _abs(base: Path, p: str) -> Path:
    q = Path(os.path.expanduser(p))
    return q if q.is_absolute() else base / q


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def wrap(cfg: dict, cmd: list[str], cwd: Path, *, network: bool = True) -> list[str]:
    """Antepone bwrap a `cmd` si el sandbox esta activo; si no, devuelve `cmd` igual."""
    if effective_mode(cfg) != "bwrap":
        return cmd
    sb = cfg["sandbox"]
    home = _home()
    cwd = Path(cwd).resolve()
    args = [
        shutil.which(sb["bwrap_bin"]) or sb["bwrap_bin"],
        "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--cap-drop", "ALL",
        "--ro-bind", "/", "/",
        "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", str(home),
    ]
    if not network:
        args.append("--unshare-net")
    for p in list(sb["ro_paths"]) + list(sb.get("extra_ro", [])):
        args += ["--ro-bind-try", str(_abs(home, p)), str(_abs(home, p))]
    for p in list(sb["rw_paths"]) + list(sb.get("extra_rw", [])):
        target = _abs(home, p)
        target.mkdir(parents=True, exist_ok=True)  # OpenCode crea sus datos la primera vez
        args += ["--bind", str(target), str(target)]
    args += ["--bind", str(cwd), str(cwd)]
    # El metadato de git va de solo lectura: el runner lo usa luego fuera del sandbox (commit).
    git_marker = cwd / ".git"
    if git_marker.is_file():  # worktree: .git es un archivo que apunta al repositorio principal
        args += ["--ro-bind", str(git_marker), str(git_marker)]
        main_git = Path(cfg.root) / ".git"
        if main_git.is_dir():
            args += ["--ro-bind", str(main_git.resolve()), str(main_git.resolve())]
    elif git_marker.is_dir():
        args += ["--ro-bind", str(git_marker), str(git_marker)]
    args += ["--chdir", str(cwd), "--"]
    return args + list(cmd)


def describe(cfg: dict) -> tuple[bool | None, str]:
    """Para `aipipe doctor`: (estado, texto)."""
    try:
        mode = requested_mode(cfg)
    except SandboxError as exc:
        return False, str(exc)
    if mode == "off":
        return None, ("AVISO: sandbox desactivado (sandbox.mode = 'off'): los agentes y los tests corren con tu usuario y pueden "
                      "leer tus archivos (incluida la clave de Linear). Solo para repositorios de prueba")
    ok, why = probe(cfg)
    if ok:
        return True, f"sandbox activo (bwrap): agentes y tests sin acceso a ~/.config/aipipe, ~/.config/gh ni ~/.ssh [{why}]"
    if mode == "bwrap":
        return False, f"sandbox.mode = 'bwrap' pero no esta disponible: {why}"
    return None, f"AVISO: sandbox no disponible ({why}); modo auto: los agentes y los tests correran SIN aislamiento"


# ------------------------------------------------------------------ autoprueba (aipipe sandbox-check)
def self_test(cfg: dict, workdir: Path, with_opencode: bool = False) -> list[tuple[bool, str]]:
    """Prueba negativa real: ejecuta dentro del sandbox lo que haria un agente manipulado y comprueba que no obtiene
    nada. Devuelve [(ok, texto)]. Usa la clave real si esta en el entorno (y, si no, una clave de mentira)."""
    results: list[tuple[bool, str]] = []
    key_env = cfg["linear"]["api_key_env"]
    real_key = os.environ.get(key_env, "")
    canary = real_key or "lin_api_CANARIO_de_aipipe_sandbox_check"
    prev = os.environ.get(key_env)
    os.environ[key_env] = canary
    try:
        mode = effective_mode(cfg)
        results.append((mode == "bwrap", f"sandbox activo (modo efectivo: {mode})"))
        if mode != "bwrap":
            return results
        workdir.mkdir(parents=True, exist_ok=True)
        env_file = _home() / ".config" / "aipipe" / "env"
        script = (
            'echo "[env]"; env; '
            'echo "[ppid]"; tr "\\0" "\\n" < /proc/$PPID/environ 2>&1; '
            'echo "[proc]"; for p in /proc/[0-9]*; do tr "\\0" "\\n" < $p/environ 2>/dev/null; done; '
            'echo "[archivo]"; cat "$HOME/.config/aipipe/env" 2>&1; '
            'echo "[gh]"; cat "$HOME/.config/gh/hosts.yml" 2>&1; '
            'echo "[ssh]"; ls "$HOME/.ssh" 2>&1; '
            f'echo "[runner]"; test -d /proc/{os.getpid()} && echo VISIBLE || echo oculto; '
            'echo "[git]"; echo x > "$PWD/.escritura" && echo ok'
        )
        argv = wrap(cfg, ["/bin/sh", "-c", script], workdir)
        proc = subprocess.run(argv, cwd=workdir, env=build_env(cfg), capture_output=True, text=True, timeout=60)
        text = proc.stdout + proc.stderr
        results.append((proc.returncode == 0, "el sandbox ejecuta comandos"))
        results.append((canary not in text, "la clave de Linear no aparece (env, /proc/$PPID/environ, /proc/*/environ)"))
        file_secret = ""
        if env_file.is_file():
            try:
                file_secret = env_file.read_text().split("=", 1)[1].strip()
            except (OSError, IndexError):
                file_secret = ""
        results.append((not file_secret or file_secret not in text, "el archivo ~/.config/aipipe/env no se puede leer"))
        results.append(("oauth_token" not in text, "las credenciales de gh (~/.config/gh) no se pueden leer"))
        results.append(("[ssh]\nid_" not in text, "las claves de ~/.ssh no son visibles"))
        results.append(("oculto" in text, "el proceso del runner no existe dentro del sandbox"))
        results.append(((workdir / ".escritura").exists(), "el directorio de trabajo es escribible"))
        (workdir / ".escritura").unlink(missing_ok=True)
        if with_opencode:
            binary = shutil.which(cfg["opencode"]["bin"])
            if not binary:
                results.append((False, "opencode no esta en el PATH"))
            else:
                argv = wrap(cfg, [binary, "--version"], workdir)
                p2 = subprocess.run(argv, cwd=workdir, env=build_env(cfg), capture_output=True, text=True, timeout=60)
                results.append((p2.returncode == 0, f"opencode arranca dentro del sandbox: {(p2.stdout or p2.stderr).strip()[:80]}"))
    finally:
        if prev is None:
            os.environ.pop(key_env, None)
        else:
            os.environ[key_env] = prev
    return results
