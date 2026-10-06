"""Configuracion en capas: defaults < global (~/.config/aipipe/config.toml) < proyecto (.aipipe.toml)."""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from .paths import global_config_path

PROJECT_FILE = ".aipipe.toml"

# Comandos que un agente de implementacion puede ejecutar por defecto (patrones glob de OpenCode, se les anade "*"
# al renderizar). Se amplia por proyecto con [project].bash_allow y con el primer token del test_command. Es una lista
# blanca: todo lo demas queda denegado. Consulta docs/05-seguridad.md antes de tocar esto.
#
# Aviso: la lista blanca tiene resquicios por diseno. `find`, `sed`, `awk`, `make`, `npm`, `pip` y `python script.py`
# (o `node script.js`) siguen pudiendo ejecutar codigo del proyecto; el cinturon `deny` de la plantilla solo cierra las
# formas inline mas obvias (`python -c`, `python3 -c`, `node -e`, `bash -c`, `sh -c`, `find * -exec`). La proteccion dura
# es el sandbox (docs/05-seguridad.md) y, cuando llegue, el proxy de red (ALB-31).
DEFAULT_BASH_ALLOW: list[str] = [
    # lectura / exploracion
    "cat", "ls", "head", "tail", "grep", "find", "sed", "awk", "wc", "sort", "diff",
    "file", "tree", "echo", "printf", "tr", "cut", "jq",
    # git de lectura y preparacion (nada de commit/push/reset: lo hace el orquestador)
    "git status", "git diff", "git log", "git show", "git add",
    # ejecutables comunes
    "python", "python3", "pytest", "pip", "uv", "node", "npm", "pnpm", "npx", "yarn",
    "ruff", "eslint", "make",
]

DEFAULTS: dict = {
    "linear": {
        "team": "",
        "trigger_label": "ai-ready",
        "trigger_states": ["Todo"],
        "state_in_progress": "In Progress",
        "state_in_review": "In Review",
        "failed_label": "ai-failed",
        "api_url": "https://api.linear.app/graphql",
        "api_key_env": "LINEAR_API_KEY",
        "max_per_batch": 5,
        # Seguridad: el contenido de una issue es entrada NO confiable para un agente con shell. Por defecto solo se
        # ejecutan issues creadas por el dueño de la API key. `allowed_creators` (emails o ids de usuario de Linear)
        # lo sustituye; `allow_any_creator = true` desactiva el filtro (no recomendado).
        "allowed_creators": [],
        "allow_any_creator": False,
        # Fases visibles en la issue (etiquetas auto-creadas): ai-phase-plan / -impl / -review, y ai-waiting si espera.
        "phase_labels": True,
        "phase_prefix": "ai-phase-",
        "waiting_label": "ai-waiting",
        "approve_label": "ai:approve",  # con esta etiqueta, el ticket pide aprobacion del plan (si after_plan = "label")
    },
    "checkpoints": {
        # "label" (por defecto): solo los tickets con `approve_label`; "always": todos; "never": ninguno.
        "after_plan": "label",
        "max_plan_revisions": 2,  # veces que se puede pedir "cambios: ..." antes de tener que aprobar o rechazar
    },
    # rol -> modelo (formato de OpenCode: proveedor/modelo). Los agentes se generan desde aqui.
    "models": {
        "triage": "opencode-go/mimo-v2.6-flash",
        "plan": "opencode-go/minimax-m3",
        "light": "opencode-go/glm-5.3-flash",
        "standard": "opencode-go/kimi-k2.7-code",
        "heavy": "opencode-go/deepseek-v4-pro",
        "reviewer": "opencode-go/glm-5.3",
    },
    "project": {
        "base_branch": "main",
        "branch_prefix": "ai/",
        "test_command": "",
        "bash_allow": [],  # comandos extra para la lista blanca del implementador (ademas de DEFAULT_BASH_ALLOW)
        "test_timeout_s": 900,
        "worktrees_dir": "",  # vacio = <datos de aipipe>/worktrees/<repo>
        "fetch": True,
        "push": True,
        "pr": True,
        "max_attempts": 3,
        "escalate_after": 2,  # a partir del intento N sube de tier
        "use_review": True,
        "keep_worktree_on_fail": True,
        "diff_chars_for_review": 24000,
    },
    "budget": {
        # Ventanas de Go: 5h = 20% del limite mensual, semana = 50%, mes = 100%.
        "soft_stop": 0.85,  # pausa todo al llegar a esta fraccion de cualquier ventana
        "standard_stop": 0.70,  # por encima, el tier standard baja a light
        "heavy_stop": 0.50,  # por encima, el tier heavy baja a standard
    },
    "runner": {
        # Ejecuciones simultaneas en TODA la maquina (todos los repos). 1 = en cola de uno en uno, lo prudente con
        # los topes de Go: dos agentes a la vez gastan cupo el doble de rapido. Ademas, nunca hay dos en el mismo repo.
        "max_concurrent": 1,
        # Cada cuantos segundos se mira en Linear si alguien saco la issue de "In Progress" (= parar el trabajo).
        "cancel_check_s": 20,
    },
    "opencode": {
        "bin": "opencode",
        "extra_args": [],
        "timeout_s": {"triage": 300, "plan": 600, "light": 900, "standard": 1800, "heavy": 2400, "review": 600},
    },
    # Aislamiento de lo que ejecuta codigo no confiable (agentes y tests). Ver sandbox.py y docs/05-seguridad.md.
    "sandbox": {
        # "auto": bwrap si funciona y, si no, SIN aislamiento (con aviso); "bwrap": obligatorio (error si no hay); "off".
        # La variable AIPIPE_SANDBOX=off|auto|bwrap tiene prioridad (tests, depuracion).
        "mode": "auto",
        "bwrap_bin": "bwrap",
        # Variables de entorno que SI llegan al agente y a los tests (patrones fnmatch). Nada mas.
        "env_allow": ["PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "LC_*", "TERM", "TZ", "TMPDIR",
                      "PWD", "OPENCODE_*", "XDG_*", "NO_COLOR", "CI", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                      "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
                      "REQUESTS_CA_BUNDLE", "PYTHON*", "VIRTUAL_ENV", "NODE_ENV", "NODE_OPTIONS"],
        "env_allow_extra": [],  # para anadir sin repetir la lista de arriba
        # Rutas (relativas al HOME o absolutas) visibles dentro del sandbox. El resto del HOME aparece vacio.
        # ~/.config/opencode NO se monta entero: OpenCode escribe ahi al arrancar (.gitignore, dependencias) y fallaria en solo
        # lectura. Solo se montan, de solo lectura, las definiciones de los agentes y su configuracion; el resto de
        # ~/.config/opencode es efimero (vive en el HOME vacio del sandbox) y un agente no puede alterar sus permisos.
        "ro_paths": [".opencode", ".config/opencode/agents", ".config/opencode/opencode.json", ".config/opencode/opencode.jsonc", "venv"],
        "rw_paths": [".local/share/opencode", ".local/state/opencode", ".cache/opencode"],
        "extra_ro": [],
        "extra_rw": [],
        "tests_network": True,  # False: los tests corren sin red (--unshare-net)
    },
    "pricing": {},  # sobrescribir precios: [pricing."modelo"] input=, output=, cache_read=, cache_write=, limit=
}


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config(dict):
    root: Path
    sources: list[Path]

    def model_for(self, role: str) -> str:
        return self["models"][role]


def find_project_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / PROJECT_FILE).exists() or (candidate / ".git").exists():
            return candidate
    return cur


def _read(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load(project_dir: Path | None = None) -> Config:
    root = find_project_root(project_dir)
    data = copy.deepcopy(DEFAULTS)
    sources: list[Path] = []
    for path in (global_config_path(), root / PROJECT_FILE):
        if path.exists():
            data = deep_merge(data, _read(path))
            sources.append(path)
    cfg = Config(data)
    cfg.root = root
    cfg.sources = sources
    return cfg
