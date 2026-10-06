import fnmatch
import re
from datetime import datetime, timedelta, timezone

import pytest

from aipipe import config as cfgmod
from aipipe.budget import Budget, fractions
from aipipe.gitops import slugify
from aipipe.ledger import Entry, Ledger
from aipipe.opencode import extract_text, extract_usage, parse_events
from aipipe.pipeline import UNTRUSTED_INPUT, impl_prompt, plan_prompt, review_prompt, triage_prompt
from aipipe.pricing import Tokens, deepseek_off_peak, estimate_cost, price_for
from aipipe.router import check_models, parse_json_block, route
from aipipe.tickets import Ticket


# ---- precios ----------------------------------------------------------------
def test_estimate_cost_matches_go_doc_example():
    # GLM-5.3-Flash: 1000 in, 55000 cached, 200 out por peticion -> la doc da ~31.580 peticiones con 60$
    t = Tokens(input=1000, output=200, cache_read=55000, requests=1)
    cost = estimate_cost("opencode-go/glm-5.3-flash", t, datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert 60 / cost == pytest.approx(31580, rel=0.02)


def test_kimi_k3_matches_go_doc_example():
    t = Tokens(input=1050, output=300, cache_read=76500, requests=1)
    cost = estimate_cost("kimi-k3", t, datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert 15 / cost == pytest.approx(490, rel=0.02)


def test_deepseek_off_peak_halves_price():
    t = Tokens(input=1_000_000)
    peak = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)      # lunes 07 UTC = pico
    valley = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)   # lunes 14 UTC = valle
    weekend = datetime(2026, 10, 3, 7, 0, tzinfo=timezone.utc)   # sabado
    assert not deepseek_off_peak(peak) and deepseek_off_peak(valley) and deepseek_off_peak(weekend)
    assert estimate_cost("deepseek-v4-pro", t, valley) == pytest.approx(estimate_cost("deepseek-v4-pro", t, peak) / 2)


def test_unknown_model_is_priced_conservatively():
    price, known = price_for("opencode-go/modelo-nuevo")
    assert not known and price.out == 15.0 and price.limit == 15


def test_pricing_override():
    price, known = price_for("kimi-k3", {"kimi-k3": {"input": 1.0}})
    assert known and price.inp == 1.0 and price.out == 15.0


# ---- parseo de salida de OpenCode ------------------------------------------------
STDOUT = "\n".join([
    '{"type":"step_start","part":{"type":"step-start"}}',
    '{"type":"text","part":{"type":"text","text":"hola"}}',
    '{"type":"step_finish","part":{"type":"step-finish","tokens":{"input":10,"output":5,"reasoning":2,"cache":{"read":100,"write":7}}}}',
    '{"type":"step_finish","part":{"type":"step-finish","tokens":{"input":1,"output":1,"cache":{"read":1,"write":0}}}}',
    "linea que no es json",
])


def test_extract_usage_sums_steps():
    ev = parse_events(STDOUT)
    t = extract_usage(ev)
    assert (t.input, t.output, t.reasoning, t.cache_read, t.cache_write, t.requests) == (11, 6, 2, 101, 7, 2)
    assert extract_text(ev, STDOUT) == "hola"


def test_extract_usage_generic_fallback_and_none():
    ev = [{"info": {"tokens": {"input": 3, "output": 4, "cache": {"read": 5, "write": 0}}}}]
    assert extract_usage(ev).input == 3
    assert extract_usage([{"type": "text"}]).requests == 0


# ---- router -----------------------------------------------------------------------
def test_parse_json_block_variants():
    assert parse_json_block('texto {"a": 1} mas') == {"a": 1}
    assert parse_json_block('```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    assert parse_json_block("sin json") is None
    assert parse_json_block("") is None


def test_route_label_wins():
    r = route(["ai:heavy"], "typo", None, {"complexity": 1})
    assert r.tier == "heavy" and r.needs_plan


def test_route_triage_rules():
    assert route([], "x", None, {"complexity": 1}).tier == "light"
    assert route([], "x", None, {"complexity": 3}).tier == "standard"
    assert route([], "x", None, {"complexity": 4}).tier == "heavy"
    assert route([], "x", None, {"complexity": 2, "risk": "high"}).tier == "heavy"
    assert route([], "x", None, {"complexity": 1, "files_estimate": 12}).tier == "standard"
    assert route([], "x", 13, {"complexity": 3}).tier == "heavy"
    assert route([], "x", None, {"complexity": "raro"}).tier == "standard"  # valor invalido -> 3


def test_route_heuristic_without_triage():
    assert route([], "Arreglar typo en README", None, None).tier == "light"
    assert route([], "Refactor del modulo de pagos", None, None).tier == "heavy"
    assert route([], "Anadir endpoint de usuarios", None, None).tier == "standard"


def test_check_models_flags_training_model():
    assert check_models({"light": "opencode-go/muse-spark-1.3-contributor"})
    assert not check_models({"light": "opencode-go/glm-5.3-flash"})


# ---- presupuesto -----------------------------------------------------------------------
def _entry(ts, normalized, cost=1.0):
    return Entry(ts=ts, issue="X", role="r", model="m", requests=1, tokens={}, cost_usd=cost, normalized=normalized)


def test_budget_thresholds(home):
    cfg = cfgmod.load()
    b = Budget(cfg, Ledger())
    now = datetime.now(timezone.utc)
    assert b.decide("heavy", now).action == "ok"
    b.ledger.append(_entry(now - timedelta(minutes=10), 0.12))  # 60% de la ventana 5h (cap 20%)
    d = b.decide("heavy", now)
    assert d.action == "downgrade" and d.tier == "standard"
    b.ledger.append(_entry(now - timedelta(minutes=5), 0.03))   # 75%
    d = b.decide("heavy", now)
    assert d.action == "downgrade" and d.tier == "light"
    assert b.decide("light", now).action == "ok"
    b.ledger.append(_entry(now - timedelta(minutes=1), 0.03))   # 90% -> pausa
    d = b.decide("light", now)
    assert d.action == "pause" and d.resume_at and d.resume_at > now


def test_budget_resume_estimate_is_when_oldest_expires(home):
    cfg = cfgmod.load()
    b = Budget(cfg, Ledger())
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=4)
    b.ledger.append(_entry(old, 0.18))          # 90% de 5h
    d = b.decide("light", now)
    assert d.action == "pause"
    assert d.resume_at == old + timedelta(hours=5)


def test_budget_record_normalizes_by_model_limit(home):
    cfg = cfgmod.load()
    b = Budget(cfg, Ledger())
    t = Tokens(input=1_000_000, requests=1)
    e15 = b.record("A", "r", "opencode-go/glm-5.3", t)       # 15$ de limite, 1.40$/M
    e60 = b.record("A", "r", "opencode-go/glm-5.2", t)       # 60$ de limite, mismo precio
    assert e15.normalized == pytest.approx(4 * e60.normalized)


def test_free_models_cost_nothing(home):
    b = Budget(cfgmod.load(), Ledger())
    e = b.record("A", "r", "opencode-go/longcat-2.5-preview-free", Tokens(input=10**6, output=10**6, requests=1))
    assert e.cost_usd == 0 and e.normalized == 0


def test_add_external_usd(home):
    b = Budget(cfgmod.load(), Ledger())
    b.add_external_usd(6.0)
    assert b.status()["month"] == pytest.approx(0.10)


# ---- config / utilidades -------------------------------------------------------------------
def test_config_layers(home, tmp_path, monkeypatch):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".git").mkdir()
    (home / "config.toml").write_text('[models]\nstandard = "opencode-go/minimax-m3"\n[budget]\nsoft_stop = 0.9\n')
    (proj / ".aipipe.toml").write_text('[budget]\nsoft_stop = 0.8\n[linear]\nteam = "ABC"\n')
    cfg = cfgmod.load(proj)
    assert cfg["models"]["standard"] == "opencode-go/minimax-m3"   # global
    assert cfg["models"]["heavy"] == "opencode-go/deepseek-v4-pro"  # default intacto
    assert cfg["budget"]["soft_stop"] == 0.8                         # proyecto gana al global
    assert cfg["linear"]["team"] == "ABC" and cfg.root == proj.resolve()


def test_slugify():
    assert slugify("Añadir búsqueda: ¡rápida!") == "anadir-busqueda-rapida"
    assert slugify("???") == "task"
    assert len(slugify("a" * 100)) == 40


# ---- regresion: opencode run se cuelga si stdin es una tuberia abierta (verificado con opencode 1.18.34) ----
def test_run_agent_closes_stdin(monkeypatch, tmp_path):
    import subprocess
    from aipipe import opencode

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(opencode.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode.subprocess, "run", fake_run)
    cfg = cfgmod.load(tmp_path)
    opencode.run_agent(cfg, "aipipe-triage", "hola", tmp_path, "triage")
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["cmd"][1:3] == ["run", "--agent"] and "--format" in seen["cmd"] and "--auto" in seen["cmd"]


def test_real_opencode_event_sample_parses():
    """Eventos capturados de `opencode run --format json` real (1.18.34)."""
    from pathlib import Path
    sample = (Path(__file__).parent / "fixtures" / "real_opencode_events.jsonl").read_text()
    ev = parse_events(sample)
    t = extract_usage(ev)
    assert t.requests == 1 and t.input == 200 and t.output == 40 and t.cache_read == 1000   # input NO incluye la cache
    assert parse_json_block(extract_text(ev, sample))["complexity"] == 2


def test_run_agent_pins_working_directory(monkeypatch, tmp_path):
    """Regresion grave: sin PWD/--dir el agente editaba el checkout principal en vez del worktree."""
    import subprocess
    from aipipe import opencode

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setenv("PWD", "/directorio/obsoleto")
    monkeypatch.setattr(opencode.shutil, "which", lambda name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode.subprocess, "run", fake_run)
    wt = tmp_path / "worktree"
    wt.mkdir()
    opencode.run_agent(cfgmod.load(tmp_path), "aipipe-impl-std", "x", wt, "standard")
    assert seen["cwd"] == wt and seen["env"]["PWD"] == str(wt)
    assert seen["cmd"][seen["cmd"].index("--dir") + 1] == str(wt)


def test_tests_import_this_checkout_not_an_installed_copy():
    """Si aipipe esta instalado con pip, los tests (y los agentes que modifican aipipe) probarian la copia instalada en vez
    del worktree. pyproject fija pythonpath = src para evitarlo."""
    from pathlib import Path

    import aipipe

    assert Path(aipipe.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1] / "src")


def test_version_matches_pyproject():
    """La version publicada (pyproject.toml) y __version__ deben coincidir (0.6.0)."""
    import tomllib
    from pathlib import Path

    import aipipe

    root = Path(__file__).resolve().parents[1]
    with open(root / "pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    assert pyproject["project"]["version"] == aipipe.__version__ == "0.6.0"


def test_untrusted_input_rule_in_all_prompts():
    t = Ticket(identifier="ALB-TEST", title="Test", description="Desc")
    prompts = [
        triage_prompt(t),
        plan_prompt(t),
        plan_prompt(t, feedback="cambia esto", previous="plan anterior"),
        impl_prompt(t, "plan", "feedback"),
        review_prompt(t, "diff"),
    ]
    for prompt in prompts:
        assert UNTRUSTED_INPUT in prompt


def test_doctor_reports_delivery_settings(tmp_path, monkeypatch, capsys):
    import subprocess

    from aipipe import cli

    monkeypatch.setenv("AIPIPE_HOME", str(tmp_path / "h"))
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    (r / ".aipipe.toml").write_text('[linear]\nteam = "ENG"\n[project]\nbase_branch = "develop"\npush = true\npr = false\n')
    monkeypatch.chdir(r)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "entrega: push activo, base=develop pero el repositorio NO tiene remoto" in out and "[ERR]" in out
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "x.git")], cwd=r, check=True)
    cli.main(["doctor"])
    assert "entrega: push activo, base=develop, remoto=origin" in capsys.readouterr().out


def test_doctor_reports_base_branch_and_remote(tmp_path, monkeypatch, capsys):
    import subprocess

    from aipipe import cli

    monkeypatch.setenv("AIPIPE_HOME", str(tmp_path / "h"))
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    (r / ".aipipe.toml").write_text('[linear]\nteam = "ENG"\n[project]\nbase_branch = "develop"\npush = true\npr = false\n')
    monkeypatch.chdir(r)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "base=" in out
    assert "NO tiene remoto" in out

    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "x.git")], cwd=r, check=True)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "base=" in out
    assert "remoto=origin" in out

    (r / ".aipipe.toml").write_text('[linear]\nteam = "ENG"\n[project]\nbase_branch = "develop"\npush = false\npr = false\n')
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "base=" in out
    assert "push desactivado" in out


# ---- lista blanca de bash de los agentes de implementacion (ALB-30) ---------------------------------
IMPL_AGENTS = ["aipipe-impl-light.md", "aipipe-impl-std.md", "aipipe-impl-heavy.md"]


def _render_impl(tmp_path, home, toml='[project]\ntest_command = ""\n'):
    from aipipe import agents

    p = tmp_path / "p"
    p.mkdir(exist_ok=True)
    (p / ".git").mkdir(exist_ok=True)
    (p / ".aipipe.toml").write_text(toml)
    return agents.render_all(cfgmod.load(p))


def _bash_rules(content: str) -> list[tuple[str, str]]:
    """Extrae en orden las reglas `"patron*": allow|deny` del bloque `bash:` de una plantilla renderizada."""
    rules: list[tuple[str, str]] = []
    for line in content.splitlines():
        m = re.match(r'^\s{4}"([^"]+)":\s*(allow|deny)\s*$', line)
        if m:
            rules.append((m.group(1), m.group(2)))
    return rules


def _last_rule_wins(rules: list[tuple[str, str]], command: str) -> bool:
    """Evaluador con la semantica de OpenCode: gana la ULTIMA regla que coincide (comparacion por `fnmatch`)."""
    decision = None
    for pattern, action in rules:
        if fnmatch.fnmatch(command, pattern):
            decision = action
    return decision == "allow"


def test_impl_agents_use_allowlist_not_denylist(home, tmp_path):
    rendered = _render_impl(tmp_path, home)
    for name in IMPL_AGENTS:
        content = rendered[name]
        assert '  bash:\n    "*": deny' in content
        assert '"*": allow' not in content
        for cmd in cfgmod.DEFAULT_BASH_ALLOW:
            assert f'"{cmd}*": allow' in content
        # bash/sh/xargs salieron de la lista blanca (ALB-42)
        for not_allowed in ("bash", "sh", "xargs"):
            assert f'"{not_allowed}*": allow' not in content
        # cinturon: red, destructivos y ejecucion arbitraria siguen denegados
        for deny in ("curl", "wget", "ssh", "scp", "sudo", "rm -rf", "python -c", "python3 -c", "node -e",
                     "perl", "bash -c", "sh -c", "find * -exec",
                     "git push", "git commit", "git checkout", "git reset", "git clean", "git rebase", "git merge"):
            assert f'"{deny}*": deny' in content


def test_project_bash_allow_extends_default(home, tmp_path):
    rendered = _render_impl(tmp_path, home, '[project]\ntest_command = ""\nbash_allow = ["mypy"]\n')
    std = rendered["aipipe-impl-std.md"]
    assert '"mypy*": allow' in std
    # un comando no listado no se permite
    assert '"nc*": allow' not in std and '"git commit*": allow' not in std


def test_test_command_first_token_is_auto_allowlisted(home, tmp_path):
    rendered = _render_impl(tmp_path, home, '[project]\ntest_command = "tox -q"\n')
    assert '"tox*": allow' in rendered["aipipe-impl-std.md"]


def test_python_and_node_projects_keep_tests_and_lint(home, tmp_path):
    py = _render_impl(tmp_path, home, '[project]\ntest_command = "pytest -q"\nbash_allow = ["ruff"]\n')["aipipe-impl-std.md"]
    node = _render_impl(tmp_path, home, '[project]\ntest_command = "npm test --silent"\nbash_allow = ["eslint"]\n')["aipipe-impl-std.md"]
    for cmd in ("pytest", "pip", "uv", "ruff", "python", "python3"):
        assert f'"{cmd}*": allow' in py
    for cmd in ("npm", "npx", "pnpm", "yarn", "eslint", "node"):
        assert f'"{cmd}*": allow' in node


def test_deny_rules_always_win_over_allowlist(home, tmp_path):
    # aunque el proyecto anada estos comandos a bash_allow, el cinturon de denegados los mantiene fuera. El evaluador
    # de OpenCode es "gana la ultima regla que coincide", asi que el deny debe ir DESPUES del allow para imponerse.
    rendered = _render_impl(tmp_path, home, '[project]\ntest_command = ""\nbash_allow = ["curl", "rm -rf", "git push", "bash -c"]\n')["aipipe-impl-std.md"]
    rules = _bash_rules(rendered)
    for pattern in ("curl", "rm -rf", "git push", "bash -c"):
        assert f'"{pattern}*": deny' in rendered
        assert f'"{pattern}*": allow' not in rendered
    for command in ("curl", "rm -rf /", "git push", "bash -c"):
        assert not _last_rule_wins(rules, command)


def test_deny_last_rule_wins_semantics(home, tmp_path):
    """Regresion ALB-42: los `deny` van despues del `allow`, asi que el evaluador «gana la ultima» deniega lo prometido.

    Se evalua la plantilla renderizada con la misma semantica que OpenCode (ultima regla que coincide, por `fnmatch`).
    """
    rendered = _render_impl(tmp_path, home)["aipipe-impl-std.md"]
    rules = _bash_rules(rendered)
    assert rules

    denied = [
        "curl", "wget", "ssh", "scp", "sudo", "rm -rf /",
        "python -c", "python3 -c", "node -e", "bash -c", "sh -c", "perl -e",
        "git push", "git commit", "find . -exec x \\;",
    ]
    allowed = ["pytest -q", "python -m pytest", "git status", "git diff", "cat README.md"]

    for cmd in denied:
        assert not _last_rule_wins(rules, cmd), f"{cmd} deberia estar denegado"
    for cmd in allowed:
        assert _last_rule_wins(rules, cmd), f"{cmd} deberia estar permitido"
