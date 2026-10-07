import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from aipipe import cli, config as cfgmod, gitops
from aipipe.budget import Budget
from aipipe.ledger import Entry, Ledger
from aipipe.linear import LinearTracker
from aipipe.pipeline import Lock, Runner, load_wait, plan_prompt, run_batch
from aipipe.tickets import LocalTracker, Ticket

from conftest import OTHER, agents_called, human_comment, issue_node


class Rec(LocalTracker):
    """LocalTracker que ademas registra las llamadas."""
    def __init__(self, ticket):
        super().__init__(ticket, log=lambda *_: None)
        self.calls, self.comments = [], []

    def start(self, t): self.calls.append("start")
    def finish(self, t): self.calls.append("finish")
    def fail(self, t): self.calls.append("fail")
    def release(self, t): self.calls.append("release")
    def comment(self, t, body): self.comments.append(body)


def ticket(labels=(), title="Anadir feature"):
    return Ticket(identifier="ENG-1", title=title, description="Crear feature.txt con done", labels=list(labels), url="https://x/ENG-1")


def runner(tracker, **proj):
    cfg = cfgmod.load()
    cfg["project"].update(proj)
    return Runner(cfg, tracker, log=lambda *_: None), cfg


def branch_files(repo, branch):
    return subprocess.run(["git", "show", "--stat", "--format=%s", branch], cwd=repo, capture_output=True, text=True).stdout


def test_success_with_triage_plan_review(repo, fake_opencode, tmp_path):
    tr = Rec(ticket())
    r, cfg = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "done", out.message
    # complejidad 2 -> standard; triage barato, sin plan (needs_plan=false... pero tier!=light) -> ver abajo
    called = agents_called(tmp_path)
    assert called[0] == "aipipe-triage"
    assert "aipipe-impl-std" in called and called[-1] == "aipipe-review"
    assert tr.calls == ["start", "finish"]
    assert "listo para revision" in tr.comments[-1] and "ai/eng-1-anadir-feature" in tr.comments[-1]
    assert "feature.txt" in branch_files(repo, out.branch)
    assert (repo / "feature.txt").exists() is False          # el trabajo vive en la rama, no en el arbol principal
    assert out.cost_usd > 0
    entries = Ledger().read()
    assert {e.role for e in entries} >= {"aipipe-triage", "aipipe-impl-std", "aipipe-review"}
    assert sum(e.requests for e in entries) == len(called)    # una peticion contabilizada por llamada


def test_label_skips_triage_and_plan_for_light(repo, fake_opencode, tmp_path):
    tr = Rec(ticket(labels=["ai:light"]))
    r, _ = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "done"
    assert agents_called(tmp_path) == ["aipipe-impl-light", "aipipe-review"]


def test_heavy_label_runs_plan(repo, fake_opencode, tmp_path):
    tr = Rec(ticket(labels=["ai:heavy"]))
    r, _ = runner(tr)
    assert r.run_ticket(tr.ticket).status == "done"
    assert agents_called(tmp_path) == ["aipipe-plan", "aipipe-impl-heavy", "aipipe-review"]


def test_escalates_tier_after_failed_attempts(repo, fake_opencode, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GOOD_ON", "aipipe-impl-heavy")
    tr = Rec(ticket(labels=["ai:std"]))
    r, _ = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "done" and out.tier == "heavy" and out.attempts == 3
    impls = [a for a in agents_called(tmp_path) if a.startswith("aipipe-impl")]
    assert impls == ["aipipe-impl-std", "aipipe-impl-std", "aipipe-impl-heavy"]


def test_fails_after_max_attempts_and_keeps_worktree(repo, fake_opencode, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GOOD_ON", "nunca")
    tr = Rec(ticket(labels=["ai:std"]))
    r, cfg = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "failed" and out.attempts == 3
    assert tr.calls == ["start", "fail"] and "no se pudo completar" in tr.comments[-1]
    assert (gitops.worktrees_root(cfg) / "eng-1").exists()   # se conserva para inspeccion
    assert "aipipe-review" not in agents_called(tmp_path)    # nunca se paga review de algo que no pasa tests


def test_review_changes_feeds_back_then_proceeds_with_note(repo, fake_opencode, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REVIEW", '{"verdict":"changes","comments":["falta caso vacio"]}')
    tr = Rec(ticket(labels=["ai:light"]))
    r, _ = runner(tr, max_attempts=2)
    out = r.run_ticket(tr.ticket)
    assert out.status == "done" and out.attempts == 2
    assert "Review con reservas" in tr.comments[-1] and "falta caso vacio" in tr.comments[-1]


def test_go_limit_pauses_and_releases_ticket(repo, fake_opencode, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "limit")
    tr = Rec(ticket(labels=["ai:std"]))
    r, cfg = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "paused" and "limite" in out.message
    assert out.resume_at and out.resume_at > datetime.now(timezone.utc) + timedelta(hours=4)
    assert tr.calls == ["start", "release"]
    assert not (gitops.worktrees_root(cfg) / "eng-1").exists()


def _fill_ledger(frac_of_5h_window):
    Ledger().append(Entry(ts=datetime.now(timezone.utc) - timedelta(minutes=5), issue="X", role="r", model="m",
                          requests=1, tokens={}, cost_usd=1, normalized=frac_of_5h_window * 0.20))


def test_budget_pause_prevents_any_model_call(repo, fake_opencode, tmp_path):
    _fill_ledger(0.95)
    tr = Rec(ticket())
    r, _ = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "paused"
    assert agents_called(tmp_path) == []          # ni una peticion
    assert tr.calls == []                          # y el ticket ni se toca en Linear


def test_budget_downgrades_heavy_to_standard(repo, fake_opencode, tmp_path):
    _fill_ledger(0.55)
    tr = Rec(ticket(labels=["ai:heavy"]))
    r, _ = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "done" and out.tier == "standard"
    assert "aipipe-impl-heavy" not in agents_called(tmp_path)
    assert any("heavy" in n and "standard" in n for n in out.notes)


def test_lock_blocks_concurrent_run(repo, fake_opencode, tmp_path):
    lock = Lock("ENG-1")
    assert lock.acquire() and not Lock("ENG-1").acquire()
    tr = Rec(ticket())
    r, _ = runner(tr)
    assert r.run_ticket(tr.ticket).status == "skipped"
    lock.release()
    assert Lock("ENG-1").acquire()


def test_stale_lock_is_recovered(repo, home):
    l = Lock("ENG-9")
    l.path.parent.mkdir(parents=True, exist_ok=True)
    l.path.write_text("999999")        # pid inexistente
    assert l.acquire()


def test_push_to_remote(repo, fake_opencode, tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=repo, check=True, capture_output=True)
    tr = Rec(ticket(labels=["ai:light"]))
    r, _ = runner(tr, push=True, pr=False)
    out = r.run_ticket(tr.ticket)
    assert out.status == "done"
    heads = subprocess.run(["git", "branch", "--list"], cwd=bare, capture_output=True, text=True).stdout
    assert "ai/eng-1-anadir-feature" in heads


def test_push_failure_marks_ticket_failed_but_keeps_branch(repo, fake_opencode, tmp_path):
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "no-existe.git")], cwd=repo, check=True)
    tr = Rec(ticket(labels=["ai:light"]))
    r, _ = runner(tr, push=True, pr=False, fetch=False)
    out = r.run_ticket(tr.ticket)
    assert out.status == "failed" and "rama local" in out.message
    assert tr.calls[-1] == "fail"
    assert subprocess.run(["git", "rev-parse", "--verify", out.branch], cwd=repo, capture_output=True).returncode == 0


def test_run_batch_stops_at_first_pause(repo, fake_opencode, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "limit")
    t1, t2 = ticket(labels=["ai:std"]), ticket(labels=["ai:std"])
    t2.identifier = "ENG-2"
    r, _ = runner(Rec(t1))
    res = run_batch(r, [t1, t2], 5)
    assert [x.status for x in res] == ["paused"]


# ---- Linear simulado --------------------------------------------------------------------------
def test_full_flow_against_mocked_linear(repo, fake_opencode, linear_server, tmp_path):
    state, url = linear_server
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    cfg["project"].update(push=False, pr=False)
    tracker = LinearTracker(cfg)
    tickets = tracker.ready()
    assert [t.identifier for t in tickets] == ["ENG-1"] and tickets[0].labels == ["ai-ready"]
    out = Runner(cfg, tracker, log=lambda *_: None).run_ticket(tickets[0])
    assert out.status == "done", out.message
    calls = state["calls"]
    ops = [c[0] for c in calls]
    assert ops[:2] == ["Viewer", "ReadyIssues"] and "TeamStates" in ops and ops[-1] == "UpdateIssue"
    updates = [c[1] for c in calls if c[0] == "UpdateIssue"]
    assert updates[0]["input"]["stateId"] == "s-prog"                            # empieza -> In Progress
    assert updates[-1]["input"]["stateId"] == "s-rev"                            # termina -> In Review
    assert set(updates[-1]["input"]["removedLabelIds"]) == {"l-ready", "l-ai-phase-review"}   # quita disparo y fase
    assert [l["name"] for l in state["issues"][0]["labels"]["nodes"]] == []        # y la issue queda limpia
    comments = [c[1]["input"] for c in calls if c[0] == "AddComment"]
    assert comments and comments[-1]["issueId"] == "id-ENG-1" and "listo para revision" in comments[-1]["body"]
    assert set(state["auth"]) == {"lin_api_test"}                                # clave en bruto, sin Bearer
    filt = calls[1][1]["filter"]
    assert filt["labels"] == {"name": {"eq": "ai-ready"}} and filt["team"] == {"key": {"eq": "ENG"}}


def test_linear_failure_adds_failed_label_and_returns_to_todo(repo, fake_opencode, linear_server, monkeypatch):
    monkeypatch.setenv("FAKE_GOOD_ON", "nunca")
    state, url = linear_server
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    tracker = LinearTracker(cfg)
    out = Runner(cfg, tracker, log=lambda *_: None).run_ticket(tracker.ready()[0])
    assert out.status == "failed"
    upd = [c[1] for c in state["calls"] if c[0] == "UpdateIssue"][-1]["input"]
    assert upd["stateId"] == "s-todo" and upd["addedLabelIds"] == ["l-ai-failed"]
    assert set(upd["removedLabelIds"]) == {"l-ready", "l-ai-phase-impl"}
    assert [l["name"] for l in state["issues"][0]["labels"]["nodes"]] == ["ai-failed"]
    assert any(c[0] == "CreateLabel" and c[1]["input"]["name"] == "ai-failed" for c in state["calls"])


def test_ready_sorts_by_priority_and_get_by_identifier(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-3", priority=0), issue_node("ENG-2", priority=3), issue_node("ENG-4", priority=1)]
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    tr = LinearTracker(cfg)
    assert [t.identifier for t in tr.ready()] == ["ENG-4", "ENG-2", "ENG-3"]
    assert tr.get("ENG-2").identifier == "ENG-2"


# ---- filtro por creador (seguridad: el texto de la issue llega a un agente con shell) --------------
def _tracker_with(url, **linear_cfg):
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    cfg["linear"].update(linear_cfg)
    logs: list[str] = []
    return LinearTracker(cfg, log=logs.append), logs


def test_default_only_runs_issues_created_by_api_key_owner(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1"), issue_node("ENG-2", creator=OTHER), issue_node("ENG-3", creator=None)]
    tr, logs = _tracker_with(url)
    assert [t.identifier for t in tr.ready()] == ["ENG-1"]
    assert any("ENG-2" in m and "otro@x.com" in m for m in logs)       # se avisa de por que se descarta
    assert any("ENG-3" in m and "desconocido" in m for m in logs)      # creador desconocido = rechazado
    tr.ready()
    assert sum("ENG-2" in m for m in logs) == 1                        # sin avisos repetidos en `watch`
    filt = [c for c in state["calls"] if c[0] == "ReadyIssues"][0][1]["filter"]
    assert filt["creator"] == {"id": {"eq": "u-me"}}                   # y ademas se filtra en el servidor


def test_default_creator_match_is_case_insensitive_on_email(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", creator={"id": "otro-id", "name": "Yo", "email": "ME@X.COM"})]
    tr, _ = _tracker_with(url)
    assert [t.identifier for t in tr.ready()] == ["ENG-1"]


def test_get_refuses_foreign_issue_and_run_does_nothing(repo, fake_opencode, linear_server, tmp_path, capsys):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-2", creator=OTHER)]
    tr, _ = _tracker_with(url)
    from aipipe.linear import LinearError
    with pytest.raises(LinearError, match="no esta autorizado"):
        tr.get("ENG-2")
    (repo / ".aipipe.toml").write_text((repo / ".aipipe.toml").read_text().replace("[linear]", f'[linear]\napi_url = "{url}"'))
    assert cli.main(["run", "--issue", "ENG-2"]) == 2                  # error de configuracion/seguridad
    assert "no esta autorizado" in capsys.readouterr().err
    assert agents_called(tmp_path) == []                               # ningun agente llego a ejecutarse
    assert not [c for c in state["calls"] if c[0] in ("UpdateIssue", "AddComment")]   # y Linear no se toco


def test_allowed_creators_replaces_owner_and_matches_email_or_id(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1"), issue_node("ENG-2", creator=OTHER)]
    tr, _ = _tracker_with(url, allowed_creators=["Otro@x.com"])
    assert [t.identifier for t in tr.ready()] == ["ENG-2"]
    tr, _ = _tracker_with(url, allowed_creators=["u-me"])
    assert [t.identifier for t in tr.ready()] == ["ENG-1"]
    assert not any(c[0] == "Viewer" for c in state["calls"])           # con lista explicita no hace falta preguntar quien es
    filt = [c for c in state["calls"] if c[0] == "ReadyIssues"][0][1]["filter"]
    assert "creator" not in filt                                       # el servidor no filtra; la comprobacion local manda


def test_allow_any_creator_disables_filter(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1"), issue_node("ENG-2", creator=OTHER), issue_node("ENG-3", creator=None)]
    tr, _ = _tracker_with(url, allow_any_creator=True)
    assert sorted(t.identifier for t in tr.ready()) == ["ENG-1", "ENG-2", "ENG-3"]
    assert tr.get("ENG-2").creator == "otro@x.com"


# ---- filtro por proyecto (ALB-44): cada repo solo ve su proyecto de Linear --------------
def test_project_filter_collects_only_repo_project(repo, linear_server):
    state, url = linear_server
    state["issues"] = [
        issue_node("ENG-1", project="App"),
        issue_node("ENG-2", project="Api"),
        issue_node("ENG-3", project="App", labels=("ai-waiting",)),
        issue_node("ENG-4", project="Api", labels=("ai-waiting",)),
    ]
    state["issues"][2]["state"] = {"id": "s-prog", "name": "In Progress", "type": "started"}
    state["issues"][3]["state"] = {"id": "s-prog", "name": "In Progress", "type": "started"}
    tr, _ = _tracker_with(url, project="App")
    assert [t.identifier for t in tr.ready()] == ["ENG-1"]
    assert [t.identifier for t in tr.waiting()] == ["ENG-3"]
    assert all(t.project == "App" for t in tr.ready() + tr.waiting())
    filt = [c for c in state["calls"] if c[0] == "ReadyIssues"][0][1]["filter"]
    assert filt["project"] == {"name": {"eq": "App"}}


def test_project_filter_is_case_insensitive_and_uses_canonical_name(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", project="App"), issue_node("ENG-2", project="Api")]
    tr, _ = _tracker_with(url, project="app")                               # en el toml, minusculas
    assert [t.identifier for t in tr.ready()] == ["ENG-1"]                 # se recoge el del proyecto "App"
    assert all(t.project == "App" for t in tr.ready())
    filt = [c for c in state["calls"] if c[0] == "ReadyIssues"][0][1]["filter"]
    assert filt["project"] == {"name": {"eq": "App"}}                      # se envia el nombre canonico, no "app"
    ok, msg = tr.check_project()
    assert ok and "App" in msg and "encontrado en Linear" in msg           # doctor coincide con el filtro real


def test_project_filter_does_not_override_creator_filter(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", project="App"), issue_node("ENG-2", project="App", creator=OTHER)]
    tr, logs = _tracker_with(url, project="App")
    assert [t.identifier for t in tr.ready()] == ["ENG-1"]                 # el del proyecto correcto pero de otro creador sigue fuera
    assert any("ENG-2" in m and "no esta autorizado" in m for m in logs)


def test_waiting_from_other_project_is_not_collected(repo, linear_server):
    state, url = linear_server
    node = issue_node("ENG-5", project="Api", labels=("ai-waiting",))
    node["state"] = {"id": "s-prog", "name": "In Progress", "type": "started"}
    state["issues"] = [node]
    tr, _ = _tracker_with(url, project="App")
    assert tr.waiting() == []                                              # un plan de otro proyecto no puede esperar aprobacion aqui


def test_ready_warns_and_collects_nothing_when_project_missing(repo, linear_server):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", project="App")]
    tr, logs = _tracker_with(url, project="Inexistente")
    assert tr.ready() == [] and tr.waiting() == []
    assert any("no existe" in m for m in logs)                             # no se queda callado
    tr.ready()
    assert sum("no existe" in m for m in logs) == 1                        # un solo aviso por sondeo repetido


def test_run_issue_refuses_ticket_from_other_project(repo, fake_opencode, linear_server, tmp_path, capsys):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-9", project="Api")]
    (repo / ".aipipe.toml").write_text(
        (repo / ".aipipe.toml").read_text().replace(
            '[linear]\nteam = "ENG"', f'[linear]\nteam = "ENG"\nproject = "App"\napi_url = "{url}"'
        )
    )
    assert cli.main(["run", "--issue", "ENG-9"]) == 2
    err = capsys.readouterr().err
    assert "no se ejecuta" in err and "Api" in err and "App" in err
    assert agents_called(tmp_path) == []                                   # ningun agente
    assert not [c for c in state["calls"] if c[0] in ("UpdateIssue", "AddComment")]   # y Linear no se toco


def test_cli_init_writes_project(repo, home, tmp_path):
    (repo / ".aipipe.toml").unlink()
    assert cli.main(["init", "--team", "ENG", "--project", "Nombre", "--no-agents"]) == 0
    cfgtxt = (repo / ".aipipe.toml").read_text()
    assert 'project = "Nombre"' in cfgtxt
    assert cfgmod.load()["linear"]["project"] == "Nombre"                  # el toml generado es valido


def test_doctor_reports_project_and_errors_when_missing(repo, linear_server, capsys, monkeypatch):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", project="App")]
    monkeypatch.setenv("LINEAR_API_KEY", "x")
    (repo / ".aipipe.toml").write_text(
        (repo / ".aipipe.toml").read_text().replace(
            '[linear]\nteam = "ENG"', f'[linear]\nteam = "ENG"\nproject = "App"\napi_url = "{url}"'
        )
    )
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "linear.project = 'App'" in out and "encontrado en Linear" in out
    (repo / ".aipipe.toml").write_text(
        (repo / ".aipipe.toml").read_text().replace('project = "App"', 'project = "Inexistente"')
    )
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "linear.project = 'Inexistente'" in out and "no existe en Linear" in out


def test_doctor_reports_creator_policy(repo, capsys, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "x")
    cli.main(["doctor"])
    assert "dueño de la API key" in capsys.readouterr().out
    (repo / ".aipipe.toml").write_text((repo / ".aipipe.toml").read_text().replace("[linear]", "[linear]\nallow_any_creator = true"))
    cli.main(["doctor"])
    assert "allow_any_creator = true" in capsys.readouterr().out


def test_missing_api_key_is_clear_error(repo, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    from aipipe.linear import LinearError
    with pytest.raises(LinearError, match="LINEAR_API_KEY"):
        LinearTracker(cfgmod.load())


# ---- CLI ----------------------------------------------------------------------------------------
def test_cli_init_installs_agents_with_configured_models(repo, home, tmp_path, capsys):
    (repo / ".aipipe.toml").unlink()
    assert cli.main(["init", "--team", "ENG", "--test-command", "pytest -q"]) == 0
    cfgtxt = (repo / ".aipipe.toml").read_text()
    assert 'team = "ENG"' in cfgtxt and 'test_command = "pytest -q"' in cfgtxt
    agents_dir = tmp_path / "ocfg" / "agents"
    files = sorted(p.name for p in agents_dir.iterdir())
    assert files == ["aipipe-impl-heavy.md", "aipipe-impl-light.md", "aipipe-impl-std.md", "aipipe-plan.md", "aipipe-review.md", "aipipe-triage.md"]
    assert "model: opencode-go/kimi-k2.7-code" in (agents_dir / "aipipe-impl-std.md").read_text()
    assert cfgmod.load()["linear"]["team"] == "ENG"      # el toml generado es valido


def test_cli_install_agents_detects_model_change(repo, home, tmp_path):
    cli.main(["install-agents"])
    (home / "config.toml").write_text('[models]\nstandard = "opencode-go/minimax-m3"\n')
    from aipipe import agents
    assert agents.status(cfgmod.load(), tmp_path / "ocfg" / "agents")["aipipe-impl-std.md"] == "desactualizado"
    cli.main(["install-agents", "--force"])
    assert "model: opencode-go/minimax-m3" in (tmp_path / "ocfg" / "agents" / "aipipe-impl-std.md").read_text()


def test_cli_run_from_file_and_dry_run(repo, fake_opencode, tmp_path, capsys):
    f = tmp_path / "t.md"
    f.write_text("# Anadir feature\nCrear feature.txt\n")
    assert cli.main(["run", "--from-file", str(f), "--dry-run"]) == 0
    assert "standard" in capsys.readouterr().out and agents_called(tmp_path) == []
    assert cli.main(["run", "--from-file", str(f)]) == 0
    assert "done" in capsys.readouterr().out


def test_cli_run_exit_codes(repo, fake_opencode, tmp_path, monkeypatch):
    f = tmp_path / "t.md"
    f.write_text("# Tarea ai:std\nx\n")
    monkeypatch.setenv("FAKE_GOOD_ON", "nunca")
    assert cli.main(["run", "--from-file", str(f)]) == 1
    monkeypatch.setenv("FAKE_MODE", "limit")
    assert cli.main(["run", "--from-file", str(f)]) == 3


def test_cli_budget_and_doctor(repo, fake_opencode, home, capsys, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "k")
    assert cli.main(["budget", "--add-usd", "3"]) == 0
    out = capsys.readouterr().out
    assert "5h" in out and "month" in out and "ok" in out
    assert cli.main(["install-agents"]) == 0
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code == 0 and "opencode:" in out and "Use balance" in out
    assert "agentes de OpenCode instalados y al dia" in out


# ---- cancelacion: alguien saca la issue de "In Progress" mientras el agente trabaja -----------------
import os
import time


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # un zombi (hijo huerfano sin recoger) ya esta muerto a efectos practicos
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


from pathlib import Path  # noqa: E402


def _slow_opencode(tmp_path, monkeypatch):
    """OpenCode falso que lanza un proceso hijo de larga duracion y se queda esperando."""
    script = tmp_path / "slow-opencode"
    script.write_text(
        "#!{py}\nimport os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "open(os.environ['CHILD_PID_FILE'], 'w').write(str(child.pid))\n"
        "print('{{\"type\": \"step-finish\", \"part\": {{\"tokens\": {{\"input\": 10, \"output\": 5}}}}}}', flush=True)\n"
        "time.sleep(120)\n".format(py=__import__("sys").executable)
    )
    script.chmod(0o755)
    monkeypatch.setenv("CHILD_PID_FILE", str(tmp_path / "child.pid"))
    cfg = cfgmod.load()
    cfg["opencode"]["bin"] = str(script)
    return cfg


def test_run_agent_stops_and_kills_whole_process_group(repo, tmp_path, monkeypatch):
    from aipipe import opencode
    cfg = _slow_opencode(tmp_path, monkeypatch)
    pidfile = tmp_path / "child.pid"
    t0 = time.monotonic()
    res = opencode.run_agent(cfg, "aipipe-impl-std", "x", repo, "standard", should_stop=lambda: pidfile.exists(), poll_s=0.2)
    assert res.cancelled and not res.ok and time.monotonic() - t0 < 20
    assert res.tokens.requests == 1 and res.tokens.output == 5          # el gasto hasta el corte se conserva
    time.sleep(0.3)
    assert not _pid_running(int(pidfile.read_text()))                   # el proceso hijo del agente tambien murio


def test_run_agent_timeout_also_kills_group_in_polling_mode(repo, tmp_path, monkeypatch):
    from aipipe import opencode
    cfg = _slow_opencode(tmp_path, monkeypatch)
    cfg["opencode"]["timeout_s"]["standard"] = 1
    res = opencode.run_agent(cfg, "aipipe-impl-std", "x", repo, "standard", should_stop=lambda: False, poll_s=0.2)
    assert res.timed_out and not res.cancelled
    time.sleep(0.3)
    assert not _pid_running(int((tmp_path / "child.pid").read_text()))


def test_failing_stop_check_never_interrupts_the_agent(repo, fake_opencode, monkeypatch):
    from aipipe import opencode
    monkeypatch.setenv("FAKE_SLEEP", "0.6")
    def boom():
        raise RuntimeError("red caida")
    res = opencode.run_agent(cfgmod.load(), "aipipe-impl-std", "x", repo, "standard", should_stop=boom, poll_s=0.1)
    assert res.ok and not res.cancelled


def test_is_cancelled_means_issue_left_in_progress_and_network_errors_do_not_count(repo, linear_server):
    state, url = linear_server
    tr, _ = _tracker_with(url)
    t = tr.get("ENG-1")
    assert tr.is_cancelled(t) is False                                  # In Progress -> sigue
    state["cancel_after_polls"] = 0
    assert tr.is_cancelled(t) is True                                   # Canceled/Todo/Backlog... -> para
    state["state_query_fails"] = True
    assert tr.is_cancelled(t) is False                                  # un fallo de la API nunca para el trabajo


def test_full_cancel_flow_against_linear(repo, fake_opencode, linear_server, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "60")
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", labels=("ai-ready", "ai:light"))]
    state["cancel_after_polls"] = 1                                     # 1a consulta (antes del agente): sigue; luego Canceled
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    cfg["runner"]["cancel_check_s"] = 0.2
    tracker = LinearTracker(cfg, log=lambda *_: None)
    t0 = time.monotonic()
    out = Runner(cfg, tracker, log=lambda *_: None).run_ticket(tracker.ready()[0])
    assert out.status == "cancelled" and out.attempts == 1 and time.monotonic() - t0 < 30   # no esperó los 60 s del agente
    updates = [c[1]["input"] for c in state["calls"] if c[0] == "UpdateIssue"]
    assert updates[0]["stateId"] == "s-prog"                            # solo el arranque toca el estado...
    assert all("stateId" not in u for u in updates[1:])                 # ...nunca se pisa lo que puso el usuario
    assert set(updates[-1]["removedLabelIds"]) == {"l-ready", "l-ai-phase-impl"}   # se quita ai-ready (no se relanza sola) y la fase
    assert [l["name"] for l in state["issues"][0]["labels"]["nodes"]] == ["ai:light"]
    comments = [c[1]["input"]["body"] for c in state["calls"] if c[0] == "AddComment"]
    assert comments and "detenido" in comments[-1] and "ai/eng-1" in comments[-1]
    assert not any(c[0] == "CreateLabel" and c[1]["input"]["name"] == "ai-failed" for c in state["calls"])   # no se marca como fallido
    assert Lock("ENG-1").acquire()                                      # y los bloqueos quedan libres
    assert Ledger().read()                                              # el gasto hasta el corte esta anotado


class CancelAfterAgent(Rec):
    supports_cancel = True
    def __init__(self, ticket):
        super().__init__(ticket)
        self.polls = 0
    def is_cancelled(self, t):
        self.polls += 1
        return self.polls >= 2                                          # 1a (antes del agente): no; 2a (despues): si
    def abort(self, t): self.calls.append("abort")


def test_cancel_between_steps_skips_tests_and_review(repo, fake_opencode, tmp_path):
    tr = CancelAfterAgent(ticket(labels=["ai:light"]))
    r, _ = runner(tr)
    out = r.run_ticket(tr.ticket)
    assert out.status == "cancelled"
    assert agents_called(tmp_path) == ["aipipe-impl-light"]             # no hubo review ni más intentos
    assert tr.calls == ["start", "abort"] and "detenido" in tr.comments[-1]


# ---- cola: un solo trabajo por repo y limite global --------------------------------------------------
def test_second_run_on_same_repo_is_queued_without_touching_the_ticket(repo, fake_opencode, tmp_path):
    tr = Rec(ticket(labels=["ai:light"]))
    r, _ = runner(tr)
    busy = r._repo_lock()
    assert busy.acquire()
    out = r.run_ticket(tr.ticket)
    assert out.status == "queued" and "repositorio" in out.message
    assert tr.calls == [] and agents_called(tmp_path) == []             # ni start ni agentes: el ticket sigue como estaba
    assert Lock("ENG-1").acquire()                                      # el bloqueo del ticket no se quedo cogido
    Lock("ENG-1").release()
    busy.release()
    assert r.run_ticket(tr.ticket).status == "done"                     # al liberarse, se ejecuta


def test_global_limit_queues_and_is_configurable(repo, fake_opencode, tmp_path):
    tr = Rec(ticket(labels=["ai:light"]))
    r, cfg = runner(tr)
    other = Lock("slot-0")
    assert other.acquire()                                              # otra ejecucion (de otro repo) ocupa el unico hueco
    out = r.run_ticket(tr.ticket)
    assert out.status == "queued" and "limite de 1" in out.message
    assert not r._repo_lock().path.exists()                             # no deja el bloqueo del repo cogido
    cfg["runner"]["max_concurrent"] = 2
    out = r.run_ticket(tr.ticket)
    assert out.status == "done", out.message                            # con 2 huecos pasa
    assert not other.path.read_text() == "" and not Lock("slot-1").path.exists()   # y libera el hueco que cogio
    other.release()


def test_repo_locks_are_per_repository(repo, tmp_path):
    r1, cfg = runner(Rec(ticket()))
    first = r1._repo_lock().path
    cfg.root = tmp_path / "otro-repo"
    assert Runner(cfg, Rec(ticket()), log=lambda *_: None)._repo_lock().path != first


def test_run_batch_stops_when_queued(repo, fake_opencode):
    t1, t2 = ticket(labels=["ai:std"]), ticket(labels=["ai:std"])
    t2.identifier = "ENG-2"
    r, _ = runner(Rec(t1))
    assert r._repo_lock().acquire()
    res = run_batch(r, [t1, t2], 5)
    assert [x.status for x in res] == ["queued"]
    r._repo_lock().release()


def test_cli_exit_code_4_when_queued(repo, fake_opencode, tmp_path, capsys):
    ticket_file = tmp_path / "t.md"
    ticket_file.write_text("# Anadir feature ai:light\nCrear feature.txt\n")
    lock = Runner(cfgmod.load(), Rec(ticket()), log=lambda *_: None)._repo_lock()
    assert lock.acquire()
    assert cli.main(["run", "--from-file", str(ticket_file)]) == 4
    assert "queued" in capsys.readouterr().out
    lock.release()


# ---- puntos de control (ALB-14) y fases (ALB-13) contra un Linear simulado CON ESTADO ----------------------
def _env(linear_server, labels=("ai-ready", "ai:light", "ai:approve"), **ckpt):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", labels=labels)]
    cfg = cfgmod.load()
    cfg["linear"]["api_url"] = url
    cfg["project"].update(push=False, pr=False)
    cfg["checkpoints"].update(ckpt)
    tracker = LinearTracker(cfg, log=lambda *_: None)
    return state, cfg, tracker, Runner(cfg, tracker, log=lambda *_: None)


def _labels(state):
    return sorted(l["name"] for l in state["issues"][0]["labels"]["nodes"])


def _to_waiting(state, tracker, runner):
    out = runner.process(tracker.ready()[0])
    assert out.status == "waiting", out.message
    return out


def _updates(state):
    return [c for c in state["calls"] if c[0] == "UpdateIssue"]


def test_checkpoint_stops_after_the_plan_and_spends_nothing_while_waiting(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    assert agents_called(tmp_path) == ["aipipe-plan"]                    # solo el plan: nada implementado todavia
    assert _labels(state) == ["ai-waiting", "ai:approve", "ai:light"]    # sin ai-ready ni etiqueta de fase
    assert state["issues"][0]["state"]["name"] == "In Progress"
    plan_comment = state["comments"][-1]["body"]
    assert "plan propuesto" in plan_comment and "`aprobado`" in plan_comment and "`cambios:" in plan_comment
    assert tracker.ready() == [] and [t.identifier for t in tracker.waiting()] == ["ENG-1"]   # no se coge como trabajo nuevo
    w = load_wait("ENG-1")
    assert w and w.plan == "1. editar feature.txt" and w.plan_comment_id == state["comments"][-1]["id"]
    assert not list((tmp_path / "home").rglob("worktrees/*/eng-1"))      # el worktree no se queda ocupando disco


def test_waiting_ticket_without_a_reply_does_nothing_and_its_own_plan_does_not_approve(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    before = (len(_updates(state)), len(state["comments"]), agents_called(tmp_path)[:])
    for _ in range(3):                                                    # varios sondeos seguidos (watch)
        assert runner.process(tracker.waiting()[0]).status == "waiting"
    assert (len(_updates(state)), len(state["comments"]), agents_called(tmp_path)) == before


def test_approval_comment_resumes_implements_and_finishes_clean(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    human_comment(state, "id-ENG-1", "aprobado")
    out = runner.process(tracker.waiting()[0])
    assert out.status == "done", out.message
    assert agents_called(tmp_path) == ["aipipe-plan", "aipipe-impl-light", "aipipe-review"]   # el plan NO se rehace
    assert state["issues"][0]["state"]["name"] == "In Review"
    assert _labels(state) == ["ai:approve", "ai:light"]                  # sin ai-waiting ni fases
    assert load_wait("ENG-1") is None
    assert "feature.txt" in branch_files(repo, out.branch)
    assert "listo para revision" in state["comments"][-1]["body"]


def test_only_authorized_users_and_whole_comment_replies_count(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    human_comment(state, "id-ENG-1", "aprobado", user={"id": "u-other", "name": "Otro", "email": "otro@x.com"})
    human_comment(state, "id-ENG-1", "ok, luego lo miro")
    human_comment(state, "id-ENG-1", "me parece bien, aprobado?")
    assert runner.process(tracker.waiting()[0]).status == "waiting"       # ni un tercero ni frases sueltas aprueban
    assert agents_called(tmp_path) == ["aipipe-plan"]
    human_comment(state, "id-ENG-1", "  Aprobado.  ")
    assert runner.process(tracker.waiting()[0]).status == "done"


def test_changes_request_replans_and_waits_again_then_approval_works(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    first_plan = state["comments"][-1]["id"]
    human_comment(state, "id-ENG-1", "cambios: usa solo la libreria estandar")
    out = runner.process(tracker.waiting()[0])
    assert out.status == "waiting"
    assert agents_called(tmp_path) == ["aipipe-plan", "aipipe-plan"]     # un plan nuevo, nada implementado
    assert state["comments"][-1]["id"] != first_plan and "plan propuesto" in state["comments"][-1]["body"]
    assert load_wait("ENG-1").revisions == 1
    assert _labels(state) == ["ai-waiting", "ai:approve", "ai:light"]
    # el "cambios" ya atendido no se vuelve a leer; hace falta una respuesta nueva
    assert runner.process(tracker.waiting()[0]).status == "waiting" and agents_called(tmp_path).count("aipipe-plan") == 2
    human_comment(state, "id-ENG-1", "aprobado")
    assert runner.process(tracker.waiting()[0]).status == "done"


def test_plan_prompt_carries_requested_changes_and_previous_plan():
    t = ticket()
    assert "<cambios_pedidos>" not in plan_prompt(t)
    p = plan_prompt(t, "usa pytest", "1. editar a.py")
    assert "usa pytest" in p and "1. editar a.py" in p and "Rehaz el plan" in p


def test_revision_limit_forces_a_decision_without_looping(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server, max_plan_revisions=1)
    _to_waiting(state, tracker, runner)
    human_comment(state, "id-ENG-1", "cambios: a")
    assert runner.process(tracker.waiting()[0]).status == "waiting"       # revision 1 de 1
    human_comment(state, "id-ENG-1", "cambios: b")
    assert runner.process(tracker.waiting()[0]).status == "waiting"       # no hay mas revisiones
    assert agents_called(tmp_path).count("aipipe-plan") == 2
    notices = [c for c in state["comments"] if "se agotaron las revisiones" in c["body"]]
    assert len(notices) == 1
    runner.process(tracker.waiting()[0]); runner.process(tracker.waiting()[0])
    assert len([c for c in state["comments"] if "se agotaron las revisiones" in c["body"]]) == 1   # un solo aviso
    human_comment(state, "id-ENG-1", "aprobado")
    assert runner.process(tracker.waiting()[0]).status == "done"


def test_rejection_returns_to_todo_without_relaunching(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    state["issues"][0]["labels"]["nodes"].append({"id": "l-ready", "name": "ai-ready"})   # alguien lo vuelve a marcar mientras espera
    human_comment(state, "id-ENG-1", "rechazado")
    out = runner.process(tracker.waiting()[0])
    assert out.status == "rejected"
    assert state["issues"][0]["state"]["name"] == "Todo" and _labels(state) == ["ai:approve", "ai:light"]
    assert tracker.ready() == [] and tracker.waiting() == []             # ni se relanza sola ni sigue esperando
    assert agents_called(tmp_path) == ["aipipe-plan"] and load_wait("ENG-1") is None
    assert "plan rechazado" in state["comments"][-1]["body"]


def test_budget_pause_after_approval_goes_back_to_waiting_and_continues_later(repo, fake_opencode, linear_server, tmp_path, monkeypatch):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    human_comment(state, "id-ENG-1", "aprobado")
    monkeypatch.setenv("FAKE_MODE", "limit")                              # Go informa de limite al empezar a implementar
    out = runner.process(tracker.waiting()[0])
    assert out.status == "paused"
    assert "ai-waiting" in _labels(state) and load_wait("ENG-1") is not None   # el plan sigue aprobado, no se pierde
    assert "continuara solo" in state["comments"][-1]["body"]
    monkeypatch.delenv("FAKE_MODE")
    Budget(cfg)                                                           # (el limite de Go no bloquea el ledger local)
    assert runner.process(tracker.waiting()[0]).status == "done"          # la aprobacion sigue ahi: se reanuda sola


def test_lost_or_foreign_wait_state_is_ignored_safely(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server)
    _to_waiting(state, tracker, runner)
    human_comment(state, "id-ENG-1", "aprobado")
    from aipipe import pipeline
    w = load_wait("ENG-1")
    w.repo = "/otro/repositorio"
    pipeline.save_wait(w)
    n = len(_updates(state))
    out = runner.process(tracker.waiting()[0])
    assert out.status == "skipped" and len(_updates(state)) == n and agents_called(tmp_path) == ["aipipe-plan"]
    pipeline.clear_wait("ENG-1")
    assert runner.process(tracker.waiting()[0]).status == "skipped"       # sin estado local tampoco se inventa nada


def test_checkpoint_modes_label_always_never(repo, fake_opencode, linear_server, tmp_path):
    # "label" sin la etiqueta: sigue de corrido
    state, cfg, tracker, runner = _env(linear_server, labels=("ai-ready", "ai:light"))
    assert runner.process(tracker.ready()[0]).status == "done" and "aipipe-plan" not in agents_called(tmp_path)


def test_checkpoint_always_and_never(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server, labels=("ai-ready", "ai:light"), after_plan="always")
    assert runner.process(tracker.ready()[0]).status == "waiting"
    state2 = linear_server[0]
    state2["issues"] = [issue_node("ENG-5", labels=("ai-ready", "ai:light", "ai:approve"))]
    cfg["checkpoints"]["after_plan"] = "never"
    assert runner.process(tracker.ready()[0]).status == "done"            # "never" ignora hasta la etiqueta


def test_local_tracker_never_waits(repo, fake_opencode, tmp_path):
    tr = Rec(ticket(labels=["ai:light", "ai:approve"]))
    r, cfg = runner(tr)
    cfg["checkpoints"]["after_plan"] = "always"
    assert r.run_ticket(tr.ticket).status == "done"                       # sin comentarios no hay a quien pedir aprobacion


def test_phase_labels_follow_the_work_and_end_clean(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server, labels=("ai-ready",))
    assert runner.process(tracker.ready()[0]).status == "done"
    added = []
    names = state["label_names"]
    for _, v in _updates(state):
        added += [names[i] for i in v["input"].get("addedLabelIds", [])]
    assert added == ["ai-phase-plan", "ai-phase-impl", "ai-phase-review"]   # una fase detras de otra
    assert _labels(state) == []                                              # y al acabar no queda ninguna
    assert state["issues"][0]["state"]["name"] == "In Review"


def test_phase_labels_can_be_disabled(repo, fake_opencode, linear_server, tmp_path):
    state, cfg, tracker, runner = _env(linear_server, labels=("ai-ready",))
    cfg["linear"]["phase_labels"] = False
    assert runner.process(tracker.ready()[0]).status == "done"
    assert not any(c[0] == "CreateLabel" and "phase" in c[1]["input"]["name"] for c in state["calls"])


def test_removing_labels_only_sends_ones_the_issue_has(repo, fake_opencode, linear_server):
    state, cfg, tracker, runner = _env(linear_server, labels=("ai:light",))      # sin ai-ready ni fases
    t = tracker.get("ENG-1")
    tracker.finish(t)
    inp = _updates(state)[-1][1]["input"]
    assert "removedLabelIds" not in inp and "addedLabelIds" not in inp           # nada que quitar: no se manda nada


def test_cli_full_cycle_wait_then_approve(repo, fake_opencode, linear_server, tmp_path, capsys):
    state, url = linear_server
    state["issues"] = [issue_node("ENG-1", labels=("ai-ready", "ai:light", "ai:approve"))]
    (repo / ".aipipe.toml").write_text((repo / ".aipipe.toml").read_text().replace("[linear]", f'[linear]\napi_url = "{url}"'))
    assert cli.main(["run"]) == 0 and "waiting" in capsys.readouterr().out
    assert agents_called(tmp_path) == ["aipipe-plan"]
    assert cli.main(["run"]) == 0 and agents_called(tmp_path) == ["aipipe-plan"]       # sin respuesta no hace nada
    human_comment(state, "id-ENG-1", "aprobado")
    assert cli.main(["run"]) == 0
    assert "done" in capsys.readouterr().out and state["issues"][0]["state"]["name"] == "In Review"
    assert agents_called(tmp_path) == ["aipipe-plan", "aipipe-impl-light", "aipipe-review"]
