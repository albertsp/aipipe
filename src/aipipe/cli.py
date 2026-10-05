"""CLI de aipipe."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, agents, config as cfgmod, gitops, sandbox
from .budget import WINDOWS, Budget
from .linear import LinearError, LinearTracker
from .paths import global_config_path, opencode_agents_dir
from .pipeline import Runner, run_batch
from .router import check_models
from .tickets import LocalTracker, Ticket


def _fmt_dt(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%d/%m %H:%M") if dt else "?"


def _detect_base(root: Path) -> str:
    proc = gitops.git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root, check=False)
    if proc.returncode == 0 and "/" in proc.stdout:
        return proc.stdout.strip().split("/", 1)[1]
    proc = gitops.git(["rev-parse", "--abbrev-ref", "HEAD"], root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() not in ("", "HEAD") else "main"


def _require_git(cfg) -> bool:
    if not (cfg.root / ".git").exists():
        print(f"Error: {cfg.root} no es un repositorio git.", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------- comandos
def cmd_init(args) -> int:
    cfg = cfgmod.load()
    target = cfg.root / cfgmod.PROJECT_FILE
    if not _require_git(cfg):
        return 2
    if target.exists() and not args.force:
        print(f"{target} ya existe (usa --force para regenerarlo).")
    else:
        target.write_text(
            agents.project_template(args.team or "", args.test_command or "", args.base or _detect_base(cfg.root)),
            encoding="utf-8",
        )
        print(f"Creado {target}")
    if not args.no_agents:
        for name, st in agents.install(cfg, opencode_agents_dir(), force=args.force).items():
            print(f"  agente {name}: {st}")
    if not global_config_path().exists():
        print(f"Consejo: crea {global_config_path()} para cambiar modelos/presupuesto una vez para todos tus proyectos.")
    print("Siguiente paso: aipipe doctor")
    return 0


def cmd_install_agents(args) -> int:
    cfg = cfgmod.load()
    dest = (cfg.root / ".opencode" / "agents") if args.project else opencode_agents_dir()
    for name, st in agents.install(cfg, dest, force=args.force).items():
        print(f"{dest / name}: {st}")
    return 0


def cmd_doctor(args) -> int:
    cfg = cfgmod.load()
    problems = 0

    def line(ok: bool | None, text: str) -> None:
        nonlocal problems
        mark = {True: "OK ", False: "ERR", None: "-- "}[ok]
        if ok is False:
            problems += 1
        print(f"[{mark}] {text}")

    line(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]} (se necesita >= 3.11)")
    oc = shutil.which(cfg["opencode"]["bin"])
    if oc:
        ver = subprocess.run([oc, "--version"], capture_output=True, text=True).stdout.strip()
        line(True, f"opencode: {oc} {ver}")
    else:
        line(False, "opencode no esta en el PATH (https://opencode.ai/docs)")
    line(shutil.which("git") is not None, "git instalado")
    line(None if shutil.which("gh") else False if cfg["project"]["pr"] else None,
         "gh (GitHub CLI) " + ("disponible" if shutil.which("gh") else "no encontrado: solo necesario si project.pr=true"))
    line((cfg.root / ".git").exists(), f"proyecto git: {cfg.root}")
    line(bool(cfg.sources), "configuracion: " + (", ".join(str(s) for s in cfg.sources) if cfg.sources else "ninguna; ejecuta `aipipe init`"))
    line(bool(cfg["linear"]["team"]), f"linear.team = '{cfg['linear']['team']}'")
    import os

    line(bool(os.environ.get(cfg["linear"]["api_key_env"])), f"variable {cfg['linear']['api_key_env']} definida")
    st, text = sandbox.describe(cfg)
    line(st, text)
    lin = cfg["linear"]
    mode = cfg["checkpoints"]["after_plan"]
    line(None, {"label": f"punto de control: solo los tickets con la etiqueta '{lin['approve_label']}' piden aprobacion del plan",
                "always": "punto de control: TODOS los tickets piden aprobacion del plan",
                "never": "punto de control: desactivado (no se pide aprobacion)"}.get(mode, f"checkpoints.after_plan = '{mode}' no es valido (label|always|never)"))
    if mode not in ("label", "always", "never"):
        line(False, "checkpoints.after_plan debe ser label, always o never")
    if lin.get("allow_any_creator"):
        line(False, "linear.allow_any_creator = true: cualquier miembro podria hacer que el agente ejecute su texto")
    elif lin.get("allowed_creators"):
        line(True, f"solo se ejecutan issues creadas por: {', '.join(map(str, lin['allowed_creators']))}")
    else:
        line(True, "solo se ejecutan issues creadas por el dueño de la API key (por defecto)")
    line(bool(cfg["project"]["test_command"].strip()) or None, "project.test_command " + ("definido" if cfg["project"]["test_command"].strip() else "vacio: el pipeline no verificara con tests"))
    for msg in check_models(cfg["models"]):
        line(False, msg)
    dest = opencode_agents_dir()
    stt = agents.status(cfg, dest)
    proj = agents.status(cfg, cfg.root / ".opencode" / "agents")
    bad = [n for n, s in stt.items() if s != "ok" and proj.get(n) != "ok"]
    line(not bad, "agentes de OpenCode " + ("instalados y al dia" if not bad else f"pendientes: {', '.join(bad)} -> aipipe install-agents --force"))
    for tool, hint in (("codegraph", "codegraph install"), ("graft", "graft init --agents agents")):
        found = shutil.which(tool)
        line(None, f"{tool}: " + (f"instalado ({found}); recuerda `{hint}` en cada proyecto" if found else "no instalado (opcional, ahorra tokens)"))
    print("\nRecuerda (no se puede comprobar desde aqui): en la consola de OpenCode deja DESACTIVADO 'Use balance'.")
    print("Es lo que garantiza que nunca se gaste mas que la suscripcion de 10$.")
    return 1 if problems else 0


def _tracker(cfg, args):
    if getattr(args, "from_file", None):
        return LocalTracker.from_file(Path(args.from_file))
    return LinearTracker(cfg)


def cmd_route(args) -> int:
    cfg = cfgmod.load()
    text = Path(args.file).read_text(encoding="utf-8") if args.file else " ".join(args.text)
    lines = text.strip().splitlines() or [""]
    ticket = Ticket(identifier="PREVIEW", title=lines[0].lstrip("# "), description="\n".join(lines[1:]), labels=args.label or [])
    info = Runner(cfg, LocalTracker(ticket), dry_run=True).plan_only(ticket)
    for k, v in info.items():
        print(f"{k:>12}: {v}")
    return 0


def cmd_budget(args) -> int:
    cfg = cfgmod.load()
    b = Budget(cfg)
    if args.add_usd:
        b.add_external_usd(args.add_usd, args.note or "external")
        print(f"Anotados {args.add_usd:.2f}$ de uso externo.")
    fr, usd = b.status(), b.spend_usd()
    soft = cfg["budget"]["soft_stop"]
    print(f"{'ventana':<8}{'cupo usado':>12}{'gasto lista':>14}   estado")
    for name in WINDOWS:
        flag = "PAUSA" if fr[name] >= soft else "ok"
        print(f"{name:<8}{fr[name]:>11.1%}{usd[name]:>13.3f}$   {flag}")
    print("\nSolo cuenta lo ejecutado por aipipe (+ lo anotado con --add-usd). La consola de Go es la fuente autoritativa.")
    return 0


def _print_outcomes(results) -> int:
    code = 0
    print("\nResumen:")
    for r in results:
        extra = f" PR {r.pr_url}" if r.pr_url else (f" rama {r.branch}" if r.branch else "")
        print(f"  {r.identifier}: {r.status} (tier {r.tier or '-'}, {r.attempts} intento(s), ${r.cost_usd:.4f}){extra}")
        if r.message and r.status != "done":
            print(f"      {r.message}")
        if r.status == "failed":
            code = max(code, 1)
        if r.status == "paused":
            print(f"      cola pausada; reanuda aprox. {_fmt_dt(r.resume_at)}")
            code = max(code, 3)
        if r.status == "queued":
            code = max(code, 4)  # nada fallo: solo hay que esperar a que acabe la otra ejecucion
    return code


def _collect(cfg, args, tracker):
    if getattr(args, "from_file", None):
        return tracker.ready()
    if args.issue:
        return [tracker.get(i) for i in args.issue]
    return _queue(tracker)


def _queue(tracker) -> list:
    """Primero los que esperan aprobacion (revisarlos no cuesta nada) y luego los nuevos, por prioridad."""
    waiting = getattr(tracker, "waiting", None)
    return (waiting() if waiting else []) + tracker.ready()


def _check_sandbox(cfg) -> bool:
    """Falla pronto si el sandbox es obligatorio y no esta; avisa si se va a ejecutar sin aislamiento."""
    try:
        if sandbox.effective_mode(cfg) == "off" and sandbox.requested_mode(cfg) != "off":
            print("AVISO: " + sandbox.describe(cfg)[1].removeprefix("AVISO: "), file=sys.stderr)
    except sandbox.SandboxError as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return False
    return True


def cmd_run(args) -> int:
    cfg = cfgmod.load()
    if not _require_git(cfg):
        return 2
    if not _check_sandbox(cfg):
        return 2
    try:
        tracker = _tracker(cfg, args)
        tickets = _collect(cfg, args, tracker)
    except LinearError as exc:
        print(f"Error de Linear: {exc}", file=sys.stderr)
        return 2
    if not tickets:
        print("No hay tickets listos (etiqueta/estado de disparo). Nada que hacer.")
        return 0
    runner = Runner(cfg, tracker, dry_run=args.dry_run)
    limit = args.max or int(cfg["linear"]["max_per_batch"])
    if args.dry_run:
        for t in tickets[:limit]:
            print("---")
            if runner.is_waiting(t):
                print(f"{t.identifier}: espera aprobacion del plan; al ejecutar se mirara su respuesta (no se gasta nada)")
                continue
            for k, v in runner.plan_only(t).items():
                print(f"{k:>12}: {v}")
        return 0
    return _print_outcomes(run_batch(runner, tickets, limit))


def cmd_watch(args) -> int:
    cfg = cfgmod.load()
    if not _require_git(cfg):
        return 2
    if not _check_sandbox(cfg):
        return 2
    try:
        tracker = LinearTracker(cfg)
    except LinearError as exc:
        print(f"Error de Linear: {exc}", file=sys.stderr)
        return 2
    runner = Runner(cfg, tracker)
    print(f"Vigilando Linear cada {args.interval}s (Ctrl+C para salir)")
    try:
        while True:
            wait = args.interval
            try:
                tickets = _queue(tracker)
                if tickets:
                    results = run_batch(runner, tickets, int(cfg["linear"]["max_per_batch"]))
                    _print_outcomes(results)
                    paused = [r for r in results if r.status == "paused"]
                    if paused and paused[0].resume_at:
                        wait = max(args.interval, (paused[0].resume_at - datetime.now(timezone.utc)).total_seconds())
                        print(f"Cola pausada hasta ~{_fmt_dt(paused[0].resume_at)}")
            except LinearError as exc:
                print(f"Error de Linear (reintento luego): {exc}", file=sys.stderr)
            time.sleep(wait)
    except KeyboardInterrupt:
        print("\nSaliendo.")
        return 0


def cmd_sandbox_check(args) -> int:
    cfg = cfgmod.load()
    from .paths import data_dir

    try:
        results = sandbox.self_test(cfg, data_dir() / "sandbox-check", with_opencode=args.opencode)
    except sandbox.SandboxError as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 2
    for ok, text in results:
        print(f"[{'OK ' if ok else 'ERR'}] {text}")
    if args.opencode:
        print("(la prueba con un modelo real es `aipipe run` sobre un ticket de prueba con sandbox.mode = 'bwrap')")
    bad = [t for ok, t in results if not ok]
    print("\nSandbox correcto." if not bad else f"\n{len(bad)} comprobacion(es) fallida(s): NO uses aipipe con repositorios reales.")
    return 1 if bad else 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aipipe", description="Linear -> OpenCode (Go) con router de modelos y presupuesto")
    p.add_argument("--version", action="version", version=f"aipipe {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="crea .aipipe.toml e instala los agentes de OpenCode")
    s.add_argument("--team", help="clave del equipo de Linear (ENG...)")
    s.add_argument("--test-command", help="comando de tests del proyecto")
    s.add_argument("--base", help="rama base (por defecto, autodetectada)")
    s.add_argument("--no-agents", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("install-agents", help="(re)instala los agentes de OpenCode desde la tabla de modelos")
    s.add_argument("--project", action="store_true", help="en .opencode/agents del proyecto en vez de global")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_install_agents)

    s = sub.add_parser("doctor", help="comprueba el entorno")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("sandbox-check", help="prueba negativa: un agente dentro del sandbox no puede leer las claves")
    s.add_argument("--opencode", action="store_true", help="comprueba tambien que opencode arranca dentro del sandbox")
    s.set_defaults(fn=cmd_sandbox_check)

    s = sub.add_parser("route", help="ensayo del router (sin llamar a modelos)")
    s.add_argument("text", nargs="*")
    s.add_argument("--file")
    s.add_argument("--label", action="append")
    s.set_defaults(fn=cmd_route)

    s = sub.add_parser("budget", help="estado del presupuesto de Go (estimacion local)")
    s.add_argument("--add-usd", type=float, help="anota uso manual fuera de aipipe")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_budget)

    s = sub.add_parser("run", help="procesa tickets listos (una pasada)")
    s.add_argument("--issue", action="append", help="identificador concreto (ENG-12), repetible")
    s.add_argument("--from-file", help="ticket desde un markdown local, sin Linear")
    s.add_argument("--max", type=int)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("watch", help="vigila Linear y procesa tickets en bucle")
    s.add_argument("--interval", type=int, default=300)
    s.set_defaults(fn=cmd_watch)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
