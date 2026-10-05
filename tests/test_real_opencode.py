"""Prueba opcional contra el OpenCode REAL (con un LLM simulado local, sin gastar nada de Go).

Se activa con AIPIPE_REAL_OPENCODE=1 y requiere `opencode` en el PATH y un proveedor `mock/m1` configurado
cuyos modelos respondan con una llamada a bash `echo done > feature.txt`. Comprueba lo que los dobles no pueden:
que el agente escribe en el worktree y NO en el checkout principal.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from aipipe import cli, config as cfgmod

pytestmark = pytest.mark.skipif(
    os.environ.get("AIPIPE_REAL_OPENCODE") != "1" or not shutil.which("opencode"),
    reason="definir AIPIPE_REAL_OPENCODE=1 con opencode y un proveedor mock configurados",
)


def test_agent_edits_worktree_not_main_checkout(repo):
    ticket = Path(tempfile.mkdtemp()) / "t.md"
    ticket.write_text("# Anadir feature ai:light\nCrear feature.txt\n")
    cfg = cfgmod.load()
    assert cli.main(["run", "--from-file", str(ticket)]) == 0
    assert not (cfg.root / "feature.txt").exists()
