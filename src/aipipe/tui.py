"""TUI (panel de control) de aipipe.

DECISIÓN DE LIBRERÍA (ALB-41):
Se mantiene ``prompt-toolkit`` como dependencia OPCIONAL (``pip install aipipe[tui]``,
ya declarado en ``pyproject.toml``). Frente a ``curses`` (librería estándar):

* ``curses`` no trae layouts, estilos ni soporte de ratón: construir paneles, barras y
  un layout responsive que aguante resize y unicode a mano es mucho código y frágil.
* ``prompt-toolkit`` da el aspecto y la robustez con poco código.
* Al ser un extra opcional, el resto de aipipe sigue sin ninguna dependencia: la TUI
  solo se instala en el VPS con ``pip install aipipe[tui]``. No se añade dependencia
  nueva ni se modifica el comportamiento del runner.

La TUI no abre puertos ni escucha en red (solo se usa por SSH). No toca ``sandbox.py``
ni el flujo de los agentes: cada acción delega en el MISMO código del CLI
(``cli.cmd_run``, ``cli.cmd_doctor``, ``cli.cmd_sandbox_check``), sin vías paralelas.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import textwrap
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import __version__
from .budget import WINDOWS, Budget
from .ledger import Ledger
from .linear import LinearError, LinearTracker
from .paths import config_dir, state_dir
from .pipeline import load_wait
from .router import LABEL_TIERS

try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    _HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

HELP_TEXT = (
    "r: refrescar   ↑/↓: seleccionar   Enter/l: lanzar ticket   "
    "d: doctor   s: sandbox-check   ?: ayuda   q/Ctrl+C: salir"
)

HELP_FULL = [
    "ayuda:",
    "  r          refrescar los datos de Linear y del libro",
    "  ↑ / ↓      mover la selección de la cola ai-ready",
    "  Enter / l  lanzar el ticket seleccionado (aipipe run --issue ID)",
    "  d          ejecutar aipipe doctor y ver el resultado",
    "  s          ejecutar aipipe sandbox-check y ver el resultado",
    "  ?          esta ayuda",
    "  q / Ctrl+C salir (no deja procesos huérfanos ni toca ningún ticket)",
]

STYLE_DICT = {
    "title": "#ansiteal bold",
    "heading": "bold",
    "dim": "#ansidarkgray",
    "warn": "#ansiyellow",
    "err": "#ansired bold",
    "ok": "#ansigreen",
    "sel": "reverse",
    "plain": "",
}


# ----------------------------------------------------------------- carga de clave
def load_env_file() -> dict[str, str]:
    """Lee ``~/.config/aipipe/env`` (formato ``CLAVE=VALOR``, con ``#``/``;`` de comentario).

    Devuelve el diccionario de variables SIN mostrarlo nunca. Las comillas simples o
    dobles que rodeen un valor se retiran.
    """
    path = config_dir() / "env"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def ensure_env() -> dict[str, str]:
    """Carga el archivo ``env`` en ``os.environ`` (sin pisar variables ya definidas)."""
    loaded = load_env_file()
    for key, value in loaded.items():
        os.environ.setdefault(key, value)
    return loaded


def use_color() -> bool:
    """Respeta ``NO_COLOR`` (convención) y terminales ``dumb``."""
    return "NO_COLOR" not in os.environ and os.environ.get("TERM", "") != "dumb"


def _class(kind: str) -> str:
    return f"class:{kind}" if use_color() else ""


# ----------------------------------------------------------------- modelo de datos
@dataclass
class TicketView:
    identifier: str
    title: str
    description: str
    labels: list[str]
    priority: int
    url: str
    id: str = ""
    tier: str = ""
    phase: str = ""
    waiting: bool = False
    plan: str = ""
    cost_usd: float = 0.0


@dataclass
class Snapshot:
    root: str
    branch: str
    active: bool
    last_poll: str
    last_ledger: str
    queue: list[TicketView]
    current: TicketView | None
    budget: dict[str, float]
    spend: dict[str, float]
    soft_stop: float
    warning: str = ""


@dataclass
class Detail:
    identifier: str
    title: str
    description: str
    plan: str
    comments: list[dict] = field(default_factory=list)


def _tier_from_labels(labels: list[str]) -> str:
    for label in labels:
        tier = LABEL_TIERS.get(label.strip().lower())
        if tier:
            return tier
    return ""


def _phase_from_labels(labels: list[str], cfg) -> str:
    prefix = cfg["linear"]["phase_prefix"]
    for label in labels:
        if label.startswith(prefix):
            return label[len(prefix):]
    return ""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive_ticket_locks() -> list[str]:
    d = state_dir() / "locks"
    out: list[str] = []
    if not d.exists():
        return out
    for p in d.iterdir():
        if not p.name.endswith(".lock"):
            continue
        name = p.name[: -len(".lock")]
        if name.startswith("repo-") or name.startswith("slot-"):
            continue
        try:
            pid = int(p.read_text().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            out.append(name)
    return out


def _current_branch(cfg) -> str:
    root = cfg.root
    if not (root / ".git").exists():
        return "?"
    from .gitops import git

    proc = git(["rev-parse", "--abbrev-ref", "HEAD"], root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else "?"


def _cost_by_issue(entries) -> dict[str, float]:
    out: dict[str, float] = {}
    for e in entries:
        out[e.issue] = out.get(e.issue, 0.0) + e.cost_usd
    return out


def _view(ticket, cfg, costs: dict[str, float]) -> TicketView:
    return TicketView(
        identifier=ticket.identifier,
        title=ticket.title,
        description=ticket.description,
        labels=list(ticket.labels),
        priority=ticket.priority,
        url=ticket.url,
        id=ticket.id,
        tier=_tier_from_labels(ticket.labels),
        phase=_phase_from_labels(ticket.labels, cfg),
        cost_usd=costs.get(ticket.identifier, 0.0),
    )


def collect_snapshot(cfg, tracker=None) -> Snapshot:
    """Recopila el estado real (Linear + libro de ejecuciones) en un ``Snapshot``.

    Un fallo de Linear se propaga como ``LinearError`` para que el Dashboard lo pinte
    como aviso (nunca como un error que cierre la interfaz).
    """
    tracker = tracker or LinearTracker(cfg)
    ledger = Ledger()
    budget = Budget(cfg, ledger)
    entries = ledger.read()
    costs = _cost_by_issue(entries)
    last = max((e.ts for e in entries), default=None)

    queue = [_view(t, cfg, costs) for t in tracker.ready()]

    current: TicketView | None = None
    for t in tracker.waiting():
        w = load_wait(t.identifier)
        v = _view(t, cfg, costs)
        v.waiting = True
        if w:
            v.plan = w.plan
            v.tier = v.tier or w.tier
        if current is None:
            current = v

    locks = _alive_ticket_locks()
    if current is None:
        for ident in locks:
            try:
                current = _view(tracker.get(ident), cfg, costs)
            except LinearError:
                continue
            break

    return Snapshot(
        root=str(cfg.root),
        branch=_current_branch(cfg),
        active=bool(locks),
        last_poll=datetime.now(timezone.utc).strftime("%d/%m %H:%M:%S"),
        last_ledger=last.astimezone().strftime("%d/%m %H:%M") if last else "",
        queue=queue,
        current=current,
        budget=budget.status(),
        spend=budget.spend_usd(),
        soft_stop=float(cfg["budget"]["soft_stop"]),
    )


def fetch_detail(cfg, tracker, t: TicketView) -> Detail:
    plan = t.plan or ""
    if not plan:
        w = load_wait(t.identifier)
        plan = w.plan if w else ""
    comments: list[dict] = []
    try:
        comments = tracker.comments(t.id or t.identifier)
    except LinearError:
        comments = []
    return Detail(t.identifier, t.title, t.description, plan, comments)


# ----------------------------------------------------------------- render (puro)
def _wrap(text: str, width: int = 76) -> list[str]:
    return textwrap.wrap(text or "", width) or ["(sin texto)"]


def _bar(frac: float, width: int = 24) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def render_status(snap: Snapshot) -> list[str]:
    lines = [
        f"aipipe {__version__}  |  proyecto: {snap.root}  |  rama: {snap.branch}",
        f"servicio: {'ACTIVO' if snap.active else 'en reposo'}  |  último sondeo: {snap.last_poll}  |  último gasto: {snap.last_ledger or '—'}",
    ]
    cur = snap.current
    if cur:
        lines.append(f"en curso: {cur.identifier} «{cur.title}»" + ("  (esperando aprobación)" if cur.waiting else ""))
        lines.append(f"  fase: {cur.phase or '—'}  tier: {cur.tier or '—'}  coste hasta ahora: ${cur.cost_usd:.4f}")
    else:
        lines.append("en curso: (ninguno)")
    if snap.queue:
        ids = ", ".join(t.identifier for t in snap.queue[:8])
        more = f" (+{len(snap.queue) - 8})" if len(snap.queue) > 8 else ""
        lines.append(f"cola ai-ready ({len(snap.queue)}): {ids}{more}")
    else:
        lines.append("cola ai-ready: (vacía)")
    return lines


def render_budget(snap: Snapshot, bar_width: int = 24) -> list[str]:
    lines = ["— consumo de Go (estimación local) —"]
    for name in WINDOWS:
        fr = snap.budget.get(name, 0.0)
        usd = snap.spend.get(name, 0.0)
        flag = "PAUSA" if fr >= snap.soft_stop else "ok"
        lines.append(f"{name:<5} {_bar(fr, bar_width)} {fr:5.0%}  ${usd:.2f}  {flag}")
    return lines


def render_queue(snap: Snapshot, sel: int = 0) -> list[str]:
    lines = ["— cola ai-ready —"]
    if not snap.queue:
        lines.append("(vacía)")
        return lines
    for i, t in enumerate(snap.queue):
        mark = ">" if i == sel else " "
        tier = t.tier or "-"
        lines.append(f"{mark} {t.identifier}  p{t.priority}  tier:{tier}  {t.title}")
    return lines


def render_detail(detail: Detail, width: int = 76) -> list[str]:
    lines = [f"— ticket: {detail.identifier} «{detail.title}» —", "descripción:"]
    lines += _wrap(detail.description or "(sin descripción)", width)
    lines.append("plan propuesto:")
    lines += _wrap(detail.plan or "(sin plan todavía)", width)
    lines.append("comentarios recientes:")
    if detail.comments:
        for c in detail.comments[-5:]:
            who = (c.get("user") or {}).get("name") or "?"
            body = (c.get("body") or "").strip().splitlines() or [""]
            lines.append(f"[{who}] {body[0]}")
            lines += _wrap("\n".join(body[1:]), width)
    else:
        lines.append("(sin comentarios)")
    return lines


def render_help() -> str:
    return HELP_TEXT


def render_snapshot(snap: Snapshot, detail: Detail | None, log_lines: list[str], sel: int = 0, width: int = 76) -> str:
    """Vista completa en texto plano (para tests y depuración, sin tocar el terminal)."""
    blocks = [
        "\n".join(render_status(snap)),
        "\n".join(render_budget(snap)),
        "\n".join(render_queue(snap, sel)),
        "\n".join(render_detail(detail, width)) if detail else "(sin ticket seleccionado)",
        "\n".join(list(log_lines)[-20:] or ["(sin registro)"]),
    ]
    if snap.warning:
        blocks.append(snap.warning)
    return "\n\n".join(blocks)


# ----------------------------------------------------------------- dashboard
def _empty_snapshot(cfg) -> Snapshot:
    return Snapshot(
        root=str(cfg.root),
        branch=_current_branch(cfg),
        active=False,
        last_poll="",
        last_ledger="",
        queue=[],
        current=None,
        budget={},
        spend={},
        soft_stop=float(cfg["budget"]["soft_stop"]),
    )


class Dashboard:
    """Estado en memoria de la TUI. Las acciones corren en hilos *daemon* (sin procesos
    hijos): al salir con ``q``/``Ctrl+C`` no queda nada huérfano y los bloqueos de un
    proceso muerto se limpian solos en la siguiente ejecución."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.tracker: LinearTracker | None = None
        self.snapshot: Snapshot | None = None
        self.detail: Detail | None = None
        self.sel = 0
        self.warning = ""
        self.busy = False
        self.log_lines: deque[str] = deque(maxlen=200)
        loaded = ensure_env()
        lin = cfg["linear"]
        secrets = [os.environ.get(lin["api_key_env"]) or ""]
        secrets += [v for v in loaded.values() if v and len(v) >= 8]
        self._secrets = [s for s in dict.fromkeys(secrets) if s]

    def _sanitize(self, text: str) -> str:
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    def log(self, msg: str = "") -> None:
        self.log_lines.append(self._sanitize(msg))

    def _build_tracker(self) -> LinearTracker:
        if self.tracker is None:
            self.tracker = LinearTracker(self.cfg, log=self.log)
        return self.tracker

    def refresh(self) -> None:
        try:
            tracker = self._build_tracker()
            snap = collect_snapshot(self.cfg, tracker)
        except LinearError as exc:
            if self.snapshot is None:
                self.snapshot = _empty_snapshot(self.cfg)
            self.snapshot.last_poll = datetime.now(timezone.utc).strftime("%d/%m %H:%M:%S")
            self.warning = f"Aviso: no se pudo contactar con Linear ({exc}). Se reintentará."
            self.log(self.warning)
            return
        self.snapshot = snap
        self.warning = ""
        if self.sel >= len(snap.queue):
            self.sel = max(0, len(snap.queue) - 1)
        self._refresh_detail()

    def selected(self) -> TicketView | None:
        if not self.snapshot:
            return None
        if self.snapshot.queue:
            return self.snapshot.queue[self.sel]
        return self.snapshot.current

    def _refresh_detail(self) -> None:
        t = self.selected()
        if t is None:
            self.detail = None
            return
        try:
            self.detail = fetch_detail(self.cfg, self._build_tracker(), t)
        except LinearError:
            self.detail = Detail(t.identifier, t.title, t.description, t.plan, [])

    # --- acciones (misma ruta que el CLI, sin vías paralelas) ---
    def _run_cli(self, label: str, fn) -> None:
        if self.busy:
            self.log("ya hay una acción en curso; espera a que termine")
            return
        self.busy = True
        self.log(f"→ {label}")

        def work() -> None:
            out = io.StringIO()
            err = io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = fn()
            except Exception as exc:  # noqa: BLE001 - se muestra, no cierra la TUI
                self.log(f"  error: {type(exc).__name__}: {exc}")
            else:
                for line in out.getvalue().splitlines():
                    self.log("  " + line)
                for line in err.getvalue().splitlines():
                    self.log("  ! " + line)
                self.log(f"  terminado (código {code}).")
            finally:
                self.busy = False
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=work, daemon=True).start()

    def launch(self) -> None:
        t = self.selected()
        if t is None:
            self.log("no hay ticket seleccionado para lanzar")
            return
        from . import cli

        self._run_cli(
            f"lanzando {t.identifier} (misma ruta que `aipipe run --issue {t.identifier}`)",
            lambda: cli.cmd_run(argparse.Namespace(issue=[t.identifier], from_file=None, max=None, dry_run=False)),
        )

    def doctor(self) -> None:
        from . import cli

        self._run_cli("doctor", lambda: cli.cmd_doctor(argparse.Namespace()))

    def sandbox_check(self) -> None:
        from . import cli

        self._run_cli("sandbox-check", lambda: cli.cmd_sandbox_check(argparse.Namespace(opencode=False)))


# ----------------------------------------------------------------- prompt_toolkit
def _panel(dash, render_fn, style: str = "plain", height=None):
    def content():
        return [(_class(style), "\n".join(render_fn()))]

    return Window(FormattedTextControl(content), wrap_lines=True, height=height)


def build_layout(dash: Dashboard) -> Layout:
    snap = dash.snapshot or _empty_snapshot(dash.cfg)

    def status_lines():
        return render_status(dash.snapshot or snap)

    def budget_lines():
        return render_budget(dash.snapshot or snap)

    def queue_lines():
        return render_queue(dash.snapshot or snap, dash.sel)

    def detail_lines():
        if dash.detail is None:
            return ["(sin ticket seleccionado)"]
        return render_detail(dash.detail)

    def log_lines():
        lines = list(dash.log_lines)[-16:]
        if dash.warning:
            lines.append(dash.warning)
        return lines or ["(sin registro)"]

    def help_lines():
        return [HELP_TEXT]

    status = _panel(dash, status_lines, style="title", height=4)
    budget = _panel(dash, budget_lines, style="plain", height=len(WINDOWS) + 1)
    queue = _panel(dash, queue_lines, style="plain")
    detail = _panel(dash, detail_lines, style="plain")
    log = _panel(dash, log_lines, style="plain")
    helpbar = _panel(dash, help_lines, style="dim", height=1)

    divider = Window(height=1, char="─", style=_class("dim"))

    return Layout(
        HSplit(
            [
                status,
                budget,
                divider,
                VSplit([queue, Window(width=1, char="│", style=_class("dim")), detail]),
                divider,
                log,
                helpbar,
            ]
        )
    )


def _bindings(dash: Dashboard) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("q")
    @kb.add("c-c")
    def _exit(event):
        event.app.exit()

    @kb.add("r")
    def _refresh(event):
        dash.refresh()

    @kb.add("up")
    def _up(event):
        dash.sel = max(0, dash.sel - 1)
        dash._refresh_detail()

    @kb.add("down")
    def _down(event):
        n = len(dash.snapshot.queue) if dash.snapshot else 0
        if n:
            dash.sel = min(n - 1, dash.sel + 1)
        dash._refresh_detail()

    @kb.add("enter")
    @kb.add("l")
    def _launch(event):
        dash.launch()

    @kb.add("d")
    def _doctor(event):
        dash.doctor()

    @kb.add("s")
    def _sandbox(event):
        dash.sandbox_check()

    @kb.add("?")
    def _help(event):
        for line in HELP_FULL:
            dash.log(line)

    return kb


def main(argv: list[str] | None = None) -> int:
    if not _HAS_PROMPT_TOOLKIT:
        print("La interfaz interactiva necesita 'prompt-toolkit'.", file=sys.stderr)
        print("Instálala con: pip install aipipe[tui]", file=sys.stderr)
        return 2

    from . import config as cfgmod

    cfg = cfgmod.load()
    dash = Dashboard(cfg)
    dash.refresh()

    style = Style.from_dict(STYLE_DICT) if use_color() else None
    app = Application(
        layout=build_layout(dash),
        key_bindings=_bindings(dash),
        full_screen=True,
        style=style,
        refresh_interval=0.5,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
