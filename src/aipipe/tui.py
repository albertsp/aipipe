"""TUI mínima para aipipe.

Interfaz de consola interactiva que delega cada acción en el CLI principal
mediante ``subprocess.run`` para no capturar stdin/stdout y conservar los
códigos de salida.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__

try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.shortcuts import input_dialog

    _HAS_PROMPT_TOOLKIT = True
except Exception:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False


def build_menu() -> list[tuple[str, list[str], str | None]]:
    """Devuelve las entradas del menú raíz.

    Cada tupla contiene: (etiqueta, argumentos para ``aipipe``, prompt opcional).
    El placeholder ``{file}`` se sustituye por un archivo temporal cuando la
    acción necesita texto del usuario (por ejemplo, ``route``).
    """
    return [
        ("init", ["init"], None),
        ("install-agents", ["install-agents"], None),
        ("doctor", ["doctor"], None),
        ("sandbox-check", ["sandbox-check"], None),
        ("budget", ["budget"], None),
        ("route (vista previa)", ["route", "--file", "{file}"], "Texto del ticket (Ctrl+D o línea vacía para terminar):"),
        ("run --dry-run", ["run", "--dry-run"], None),
        ("watch", ["watch"], None),
        ("salir", [], None),
    ]


def run_action(args: list[str], extra: str | None = None) -> int:
    """Ejecuta ``aipipe <args>`` en un subproceso, usando un archivo temporal si se requiere input."""
    if not args:
        return 0
    new_args = list(args)
    tmp_path: str | None = None
    try:
        if "{file}" in new_args:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as fh:
                fh.write(extra or "")
                tmp_path = fh.name
            new_args = [arg if arg != "{file}" else tmp_path for arg in new_args]
        return subprocess.run([sys.executable, "-m", "aipipe", *new_args]).returncode
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


def _header_text(cfg) -> str:
    from . import config as cfgmod
    from .budget import Budget
    from .gitops import git

    root = cfg.root
    branch = "?"
    if (root / ".git").exists():
        proc = git(["rev-parse", "--abbrev-ref", "HEAD"], root, check=False)
        if proc.returncode == 0:
            branch = proc.stdout.strip() or "?"

    status = Budget(cfg).status()
    worst = max(status, key=status.get)
    fr = status[worst]
    soft = cfg["budget"]["soft_stop"]
    flag = "PAUSA" if fr >= soft else "ok"
    return (
        f"aipipe {__version__}  |  proyecto: {root}  |  rama: {branch}  |  "
        f"presupuesto [{worst}]: {fr:>6.1%} ({flag})"
    )


def _ask_input(prompt: str, title: str) -> str | None:
    return input_dialog(title=title, text=prompt).run()


def main(argv: list[str] | None = None, _select: int | None = None) -> int:
    if not _HAS_PROMPT_TOOLKIT:
        print("La interfaz interactiva necesita 'prompt-toolkit'.", file=sys.stderr)
        print("Instálala con: pip install aipipe[tui]", file=sys.stderr)
        return 2

    from . import config as cfgmod

    cfg = cfgmod.load()
    menu = build_menu()

    if _select is not None:
        label, args, prompt = menu[_select]
        extra = _ask_input(prompt, title=label) if prompt else None
        if prompt and extra is None:
            return 0
        return run_action(args, extra)

    state = {
        "idx": 0,
        "cfg": cfg,
        "mode": "menu",  # "menu" | "wait"
        "message": "",
    }

    kb = KeyBindings()

    @kb.add("q")
    def _exit(event):
        event.app.exit()

    @kb.add("up")
    def _up(event):
        if state["mode"] == "menu":
            state["idx"] = (state["idx"] - 1) % len(menu)

    @kb.add("down")
    def _down(event):
        if state["mode"] == "menu":
            state["idx"] = (state["idx"] + 1) % len(menu)

    @kb.add("r")
    def _refresh(event):
        state["cfg"] = cfgmod.load()

    @kb.add("enter")
    def _enter(event):
        if state["mode"] == "wait":
            state["mode"] = "menu"
            state["message"] = ""
            return
        label, args, prompt = menu[state["idx"]]
        if label == "salir":
            event.app.exit()
            return
        if prompt:
            event.app.exit(result=("input", state["idx"]))
            return
        state["mode"] = "wait"
        state["message"] = f"Ejecutando: aipipe {' '.join(args)} ..."
        event.app.invalidate()
        try:
            code = run_action(args)
        except Exception as exc:  # pragma: no cover
            state["message"] = f"Error: {exc}. Pulsa Enter para volver."
        else:
            state["message"] = f"Terminado con código {code}. Pulsa Enter para volver."

    def header_control():
        return [("class:title", _header_text(state["cfg"]))]

    def menu_control():
        lines = []
        for i, (label, _args, _prompt) in enumerate(menu):
            selected = i == state["idx"] and state["mode"] == "menu"
            style = "class:selected" if selected else "class:normal"
            prefix = "> " if selected else "  "
            lines.append((style, f"{prefix}{label}\n"))
        if state["mode"] == "wait":
            lines.append(("", "\n"))
            lines.append(("class:message", state["message"]))
        return lines

    layout = Layout(
        HSplit([
            Window(FormattedTextControl(header_control), height=1),
            Window(FormattedTextControl(menu_control)),
        ])
    )

    style = {
        "title": "#ansiteal bold",
        "selected": "bg:#ansiteal #ansiwhite bold",
        "normal": "",
        "message": "italic",
    }

    app = Application(layout=layout, key_bindings=kb, full_screen=True, style=style)
    result = app.run()

    if isinstance(result, tuple) and result[0] == "input":
        label, args, prompt = menu[result[1]]
        extra = _ask_input(prompt, title=label)
        if extra is None:
            return 0
        print(f"Ejecutando: aipipe {' '.join(args)} ...")
        return run_action(args, extra)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
