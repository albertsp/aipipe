"""Operaciones git/gh del orquestador. Los agentes NO commitean ni hacen push: lo hace el orquestador."""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from .paths import data_dir


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} fallo: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def slugify(text: str, limit: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:limit].strip("-") or "task"


def worktrees_root(cfg: dict) -> Path:
    custom = cfg["project"].get("worktrees_dir")
    if custom:
        return Path(custom).expanduser()
    return data_dir() / "worktrees" / cfg.root.name


def branch_name(cfg: dict, identifier: str, title: str) -> str:
    return f"{cfg['project']['branch_prefix']}{identifier.lower()}-{slugify(title)}"


def has_remote(root: Path) -> bool:
    return bool(git(["remote"], root, check=False).stdout.strip())


def base_ref(cfg: dict) -> str:
    root = cfg.root
    base = cfg["project"]["base_branch"]
    if has_remote(root):
        if cfg["project"].get("fetch", True):
            git(["fetch", "origin", base], root, check=False, timeout=180)
        if git(["rev-parse", "--verify", "--quiet", f"origin/{base}"], root, check=False).returncode == 0:
            return f"origin/{base}"
    if git(["rev-parse", "--verify", "--quiet", base], root, check=False).returncode != 0:
        raise GitError(f"No existe la rama base '{base}' (ajusta project.base_branch en .aipipe.toml)")
    return base


def create_worktree(cfg: dict, identifier: str, title: str) -> tuple[Path, str]:
    root = cfg.root
    branch = branch_name(cfg, identifier, title)
    path = worktrees_root(cfg) / identifier.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        git(["worktree", "remove", "--force", str(path)], root, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git(["worktree", "prune"], root, check=False)
    # rama de un intento anterior: se reutiliza el nombre partiendo de la base actual
    if git(["rev-parse", "--verify", "--quiet", branch], root, check=False).returncode == 0:
        git(["branch", "-D", branch], root, check=False)
    git(["worktree", "add", "-b", branch, str(path), base_ref(cfg)], root)
    return path, branch


def remove_worktree(cfg: dict, path: Path) -> None:
    git(["worktree", "remove", "--force", str(path)], cfg.root, check=False)
    shutil.rmtree(path, ignore_errors=True)


def diff(path: Path, limit: int) -> str:
    git(["add", "-A", "-N"], path, check=False)
    out = git(["diff", "HEAD"], path, check=False).stdout
    return out if len(out) <= limit else out[:limit] + f"\n... [diff truncado: {len(out) - limit} caracteres mas]"


def changed_files(path: Path) -> list[str]:
    out = git(["status", "--porcelain"], path, check=False).stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


def commit_all(path: Path, message: str) -> bool:
    """Devuelve False si no hay cambios."""
    git(["add", "-A"], path)
    if not git(["status", "--porcelain"], path).stdout.strip():
        return False
    git(["commit", "-m", message], path)
    return True


def push(path: Path, branch: str) -> None:
    git(["push", "-u", "origin", branch], path, timeout=300)


def open_pr(path: Path, base: str, title: str, body: str) -> str:
    if not shutil.which("gh"):
        raise GitError("gh no esta instalado: no se puede crear el PR (instala GitHub CLI o pon project.pr=false)")
    proc = subprocess.run(
        ["gh", "pr", "create", "--base", base, "--title", title, "--body", body],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise GitError(f"gh pr create fallo: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip().splitlines()[-1]
