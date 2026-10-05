"""Wrapper de `opencode run`. Toda llamada a los modelos de Go pasa por el CLI de OpenCode (cliente validado por Go)."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox
from .pricing import Tokens

LIMIT_RE = re.compile(
    r"(usage limit|rate.?limit|quota|too many requests|limit (?:reached|exceeded)|\b429\b|insufficient (?:credit|balance|quota))",
    re.I,
)
FINISH_TYPES = {"step-finish", "step_finish", "stepfinish"}


class OpenCodeMissing(RuntimeError):
    pass


@dataclass
class RunResult:
    text: str
    tokens: Tokens
    returncode: int
    stderr: str = ""
    limit_hit: bool = False
    timed_out: bool = False
    events: list = field(default_factory=list)
    cancelled: bool = False  # detenido porque alguien saco la issue de "In Progress"

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.limit_hit and not self.cancelled


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _tokens_from(d: dict) -> Tokens | None:
    tk = d.get("tokens")
    if not isinstance(tk, dict) or not ({"input", "output"} & tk.keys()):
        return None
    cache = tk.get("cache") if isinstance(tk.get("cache"), dict) else {}

    def num(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    return Tokens(
        input=num(tk.get("input")),
        output=num(tk.get("output")),
        reasoning=num(tk.get("reasoning")),
        cache_read=num(cache.get("read", tk.get("cache_read"))),
        cache_write=num(cache.get("write", tk.get("cache_write"))),
        requests=1,
    )


def extract_usage(events: list) -> Tokens:
    """Suma tokens de los eventos de fin de paso (una peticion al modelo por paso).

    El esquema exacto de `--format json` puede variar entre versiones: si no hay eventos de fin de paso se
    suman todos los diccionarios con `tokens`. Si no hay ninguno, el uso queda en 0 (se avisa en el log).
    """
    total = Tokens()
    found = False
    for ev in events:
        if isinstance(ev, dict) and str(ev.get("type", "")).lower() in FINISH_TYPES:
            for d in _iter_dicts(ev):
                t = _tokens_from(d)
                if t:
                    total.add(t)
                    found = True
                    break
    if found:
        return total
    seen: set[int] = set()
    for ev in events:
        for d in _iter_dicts(ev):
            if id(d) in seen:
                continue
            seen.add(id(d))
            t = _tokens_from(d)
            if t:
                total.add(t)
    return total


def extract_text(events: list, raw: str) -> str:
    parts = []
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "text":
            part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
            text = part.get("text") or ev.get("text")
            if text:
                parts.append(text)
    return "\n".join(parts) if parts else raw


def parse_events(stdout: str) -> list:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def find_bin(cfg: dict) -> str:
    name = cfg["opencode"]["bin"]
    found = shutil.which(name)
    if not found:
        raise OpenCodeMissing(f"No encuentro '{name}' en el PATH. Instala OpenCode: https://opencode.ai/docs")
    return found


def _kill_group(proc: subprocess.Popen) -> None:
    """Termina el proceso y todo su grupo (los comandos que lance el agente: tests, servidores, etc.)."""
    def send(sig):
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass

    send(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        send(getattr(signal, "SIGKILL", signal.SIGTERM))
        proc.wait()
    # el lider puede haber salido ya; se barre el grupo por si quedan hijos
    send(getattr(signal, "SIGKILL", signal.SIGTERM))


def _execute(cmd, cwd, env, timeout, should_stop, poll_s):
    """Ejecuta el comando -> (returncode, stdout, stderr, timed_out, cancelled).

    Sin `should_stop` es el `subprocess.run` de siempre. Con `should_stop` se sondea cada `poll_s` segundos y, si
    devuelve True, se mata el grupo de procesos y se devuelve lo que hubiera hasta ese momento.
    """
    # stdin=DEVNULL es imprescindible: `opencode run` lee stdin si es una tuberia y se queda bloqueado
    # esperando EOF (cron, systemd, CI, otro proceso padre...). Verificado con opencode 1.18.34.
    if should_stop is None:
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return 124, out, "timeout", True, False
        return proc.returncode, proc.stdout, proc.stderr, False, False

    popen = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,  # grupo propio: se puede matar con todos sus hijos
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            try:
                out, err = popen.communicate(timeout=max(0.05, min(poll_s, left)))
                return popen.returncode, out, err, False, False
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() >= deadline:
                _kill_group(popen)
                out, err = popen.communicate()
                return 124, out or "", "timeout", True, False
            try:
                stop = bool(should_stop())
            except Exception:  # noqa: BLE001 - un fallo al consultar nunca debe interrumpir el trabajo
                stop = False
            if stop:
                _kill_group(popen)
                out, err = popen.communicate()
                return 143, out or "", "cancelado", False, True
    except BaseException:  # Ctrl+C del usuario u otro error: no dejar el agente huerfano gastando cupo
        if popen.poll() is None:
            _kill_group(popen)
        raise


def run_agent(
    cfg: dict, agent: str, prompt: str, cwd: Path, timeout_key: str, title: str = "",
    should_stop=None, poll_s: float = 20.0,
) -> RunResult:
    binary = find_bin(cfg)
    cmd = [binary, "run", "--agent", agent, "--format", "json", "--auto", "--dir", str(cwd)]
    if title:
        cmd += ["--title", title]
    cmd += list(cfg["opencode"].get("extra_args", []))
    cmd.append(prompt)
    timeout = int(cfg["opencode"]["timeout_s"].get(timeout_key, 1800))
    # Entorno por lista blanca (nunca LINEAR_API_KEY ni tokens de GitHub) y, si procede, dentro de bwrap (ALB-27).
    # cwd= no actualiza $PWD: OpenCode resuelve su directorio de trabajo con $PWD y, sin esto, el agente
    # escribia en el checkout principal en vez de en el worktree aislado (verificado con opencode 1.18.34).
    env = sandbox.build_env(cfg, {"OPENCODE_DISABLE_AUTOUPDATE": os.environ.get("OPENCODE_DISABLE_AUTOUPDATE", "1"), "PWD": str(cwd)})
    cmd = sandbox.wrap(cfg, cmd, cwd)
    code, stdout, stderr, timed_out, cancelled = _execute(cmd, cwd, env, timeout, should_stop, poll_s)
    events = parse_events(stdout)
    text = extract_text(events, stdout)
    if timed_out or cancelled:
        return RunResult(text, extract_usage(events), code, stderr, False, timed_out, events, cancelled)
    error_text = stderr + "\n".join(
        json.dumps(e) for e in events if isinstance(e, dict) and str(e.get("type", "")).lower() == "error"
    )
    limit = bool(LIMIT_RE.search(error_text)) and (code != 0 or "error" in error_text.lower())
    return RunResult(text, extract_usage(events), code, stderr[-2000:], limit, False, events)
