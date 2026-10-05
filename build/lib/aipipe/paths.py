"""Rutas de configuracion, datos y estado. AIPIPE_HOME las reune en un solo directorio (tests)."""
from __future__ import annotations

import os
from pathlib import Path


def _base(env_var: str, fallback: str) -> Path:
    home = os.environ.get("AIPIPE_HOME")
    if home:
        return Path(home)
    root = os.environ.get(env_var)
    return Path(root) / "aipipe" if root else Path.home() / fallback / "aipipe"


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _base("XDG_DATA_HOME", ".local/share")


def state_dir() -> Path:
    return _base("XDG_STATE_HOME", ".local/state")


def global_config_path() -> Path:
    return config_dir() / "config.toml"


def ledger_path() -> Path:
    return data_dir() / "ledger.jsonl"


def opencode_agents_dir() -> Path:
    """Agentes globales de OpenCode: valen para cualquier proyecto."""
    home = os.environ.get("AIPIPE_OPENCODE_HOME")
    if home:
        return Path(home) / "agents"
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "opencode" / "agents"
