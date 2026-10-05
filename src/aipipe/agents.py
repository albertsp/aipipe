"""Genera e instala los agentes de OpenCode a partir de la tabla de modelos de la configuracion."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

AGENT_ROLE = {
    "aipipe-triage": "triage",
    "aipipe-plan": "plan",
    "aipipe-impl-light": "light",
    "aipipe-impl-std": "standard",
    "aipipe-impl-heavy": "heavy",
    "aipipe-review": "reviewer",
}


def render_all(cfg: dict) -> dict[str, str]:
    tdir = resources.files("aipipe").joinpath("templates", "agents")
    out = {}
    for name, role in AGENT_ROLE.items():
        raw = tdir.joinpath(f"{name}.md.tmpl").read_text(encoding="utf-8")
        out[f"{name}.md"] = raw.replace("{{model}}", cfg["models"][role])
    return out


def status(cfg: dict, dest: Path) -> dict[str, str]:
    result = {}
    for fname, content in render_all(cfg).items():
        path = dest / fname
        if not path.exists():
            result[fname] = "falta"
        elif path.read_text(encoding="utf-8") != content:
            result[fname] = "desactualizado"
        else:
            result[fname] = "ok"
    return result


def install(cfg: dict, dest: Path, force: bool = False) -> dict[str, str]:
    dest.mkdir(parents=True, exist_ok=True)
    current = status(cfg, dest)
    result = {}
    for fname, content in render_all(cfg).items():
        state = current[fname]
        if state == "ok":
            result[fname] = "sin cambios"
        elif state == "falta" or force:
            (dest / fname).write_text(content, encoding="utf-8")
            result[fname] = "instalado" if state == "falta" else "actualizado"
        else:
            result[fname] = "desactualizado (usa --force para sobrescribir)"
    return result


def project_template(team: str, test_command: str, base_branch: str) -> str:
    raw = resources.files("aipipe").joinpath("templates", "aipipe.toml.tmpl").read_text(encoding="utf-8")
    return (
        raw.replace("{{team}}", team)
        .replace("{{test_command}}", test_command.replace("\\", "\\\\").replace('"', '\\"'))
        .replace("{{base_branch}}", base_branch)
    )
