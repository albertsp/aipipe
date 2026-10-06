"""Genera e instala los agentes de OpenCode a partir de la tabla de modelos de la configuracion."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from .config import DEFAULT_BASH_ALLOW

AGENT_ROLE = {
    "aipipe-triage": "triage",
    "aipipe-plan": "plan",
    "aipipe-impl-light": "light",
    "aipipe-impl-std": "standard",
    "aipipe-impl-heavy": "heavy",
    "aipipe-review": "reviewer",
}


# Comandos que el implementador NUNCA puede ejecutar, aunque [project].bash_allow intente reactivarlos. Son los mismos
# del cinturon `deny` que vive en la plantilla; si tocas uno, toca el otro.
_BASH_DENY = {
    "curl", "wget", "ssh", "scp", "sudo", "rm -rf",
    "python -c", "python3 -c", "node -e", "perl", "bash -c", "sh -c",
    "find * -exec",
    "git push", "git commit", "git checkout", "git switch", "git reset", "git clean", "git rebase", "git merge",
}


def _yaml_quote(pattern: str) -> str:
    """Escapa un patron para meterlo como clave YAML entre comillas dobles."""
    return '"' + pattern.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _bash_allow_block(cfg: dict) -> str:
    """Bloque YAML `bash:` para los agentes de implementacion: `*: deny` + lista blanca.

    La lista blanca = DEFAULT_BASH_ALLOW + [project].bash_allow + primer token de test_command. Se anade "*" a cada
    patron para permitir argumentos (p. ej. `git status --short`). Los comandos de _BASH_DENY se descartan aunque se
    pidan, para que el cinturon de denegados de la plantilla no se pueda reactivar.
    """
    allow: list[str] = []
    for cmd in [*DEFAULT_BASH_ALLOW, *cfg.get("project", {}).get("bash_allow", [])]:
        if cmd not in allow and cmd not in _BASH_DENY:
            allow.append(cmd)
    test_command = cfg.get("project", {}).get("test_command", "").strip()
    if test_command:
        first = test_command.split()[0]
        if first not in allow and first not in _BASH_DENY:
            allow.append(first)
    return "\n".join(f"    {_yaml_quote(cmd + '*')}: allow" for cmd in allow)


def render_all(cfg: dict) -> dict[str, str]:
    tdir = resources.files("aipipe").joinpath("templates", "agents")
    bash_allow = _bash_allow_block(cfg)
    out = {}
    for name, role in AGENT_ROLE.items():
        raw = tdir.joinpath(f"{name}.md.tmpl").read_text(encoding="utf-8")
        content = raw.replace("{{model}}", cfg["models"][role])
        content = content.replace("{{bash_allow}}", bash_allow)
        out[f"{name}.md"] = content
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
