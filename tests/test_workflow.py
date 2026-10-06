"""Verifica la plantilla de workflow de GitHub Actions sin dependencias extras."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "deploy" / "github-actions-tests.yml"


@pytest.fixture
def wf():
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_file_exists():
    assert WORKFLOW.exists(), f"No existe {WORKFLOW}"


def test_workflow_triggers_pull_request(wf):
    assert re.search(r"^on:\s*$", wf, re.MULTILINE), "falta clave 'on'"
    assert re.search(r"^\s+pull_request:\s*\{\}\s*$", wf, re.MULTILINE), "falta trigger pull_request"


def test_workflow_triggers_push_to_main(wf):
    assert re.search(r"^\s+push:\s*$", wf, re.MULTILINE), "falta trigger push"
    assert re.search(r"^\s+branches:\s*\[main\]\s*$", wf, re.MULTILINE), "push no filtra por main"


def test_workflow_does_not_use_pull_request_target(wf):
    assert "pull_request_target" not in wf, "no debe usar pull_request_target"


def test_workflow_does_not_use_secrets(wf):
    assert "secrets." not in wf, "no debe referenciar secrets.*"


def test_workflow_runs_on_ubuntu_latest(wf):
    assert re.search(r"^\s+runs-on:\s+ubuntu-latest\s*$", wf, re.MULTILINE), "el job debe correr en ubuntu-latest"


def test_workflow_installs_bubblewrap(wf):
    assert "bubblewrap" in wf, "debe instalar bubblewrap"


def test_workflow_installs_dev_package(wf):
    assert 'pip install -e ".[dev]"' in wf, "debe instalar el paquete en modo dev"


def test_workflow_runs_full_suite(wf):
    assert "python -m pytest -q" in wf, "debe ejecutar la suite completa"


def test_workflow_guards_against_skipped_security_tests(wf):
    assert "tests/test_sandbox.py" in wf, "debe ejecutar tests/test_sandbox.py explicitamente"
    assert "SKIPPED" in wf, "debe detectar tests saltados"
    assert "exit 1" in wf, "debe fallar si se saltan tests de seguridad"
