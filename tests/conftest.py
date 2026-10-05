"""Fixtures: repo git temporal, OpenCode falso en el PATH y servidor Linear simulado."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

FAKE_OPENCODE = '''#!{python}
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
if args and args[0] == "--version":
    print("0.0.0-fake"); sys.exit(0)
agent = args[args.index("--agent") + 1]
mode = os.environ.get("FAKE_MODE", "ok")
log = Path(os.environ["FAKE_LOG"])
with log.open("a") as fh:
    fh.write(agent + "\\n")

def emit(text, inp=900, out=200, cache=50000):
    print(json.dumps({{"type": "step_start", "part": {{"type": "step-start"}}}}))
    print(json.dumps({{"type": "text", "part": {{"type": "text", "text": text}}}}))
    print(json.dumps({{"type": "step_finish", "part": {{"type": "step-finish", "cost": 0,
        "tokens": {{"input": inp, "output": out, "reasoning": 0, "cache": {{"read": cache, "write": 0}}}}}}}}))

if mode == "limit":
    sys.stderr.write("Error: usage limit reached for this plan (429)\\n"); sys.exit(1)
if agent == "aipipe-triage":
    emit(os.environ.get("FAKE_TRIAGE", '{{"complexity": 2, "files_estimate": 1, "risk": "low", "needs_plan": false, "summary": "x"}}'))
elif agent == "aipipe-plan":
    emit("1. editar feature.txt")
elif agent.startswith("aipipe-impl"):
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))   # simula un agente lento (para probar la cancelacion)
    good_on = os.environ.get("FAKE_GOOD_ON", "any")
    ok = good_on in ("any", agent)
    Path("feature.txt").write_text("done\\n" if ok else "wrong\\n")
    emit("hecho" if ok else "intento fallido")
elif agent == "aipipe-review":
    emit(os.environ.get("FAKE_REVIEW", '{{"verdict": "approve", "comments": []}}'))
else:
    emit("?")
'''


STATE_NAMES = {"s-todo": "Todo", "s-prog": "In Progress", "s-rev": "In Review"}
STATE_TYPES = {"Todo": "unstarted", "In Progress": "started", "In Review": "started"}


def label_id(name: str) -> str:
    return {"ai-ready": "l-ready"}.get(name, f"l-{name}")


class LinearMock(BaseHTTPRequestHandler):
    """Linear simulado CON ESTADO: aplica de verdad los cambios de estado y etiquetas, y guarda los comentarios."""
    state: dict = {}

    def log_message(self, *a):  # silencio
        pass

    def _issue(self, st, iid):
        return next((i for i in st["issues"] if i["id"] == iid or i["identifier"] == iid), None)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        op = body.get("operationName")
        v = body.get("variables") or {}
        st = LinearMock.state
        st.setdefault("calls", []).append((op, body.get("variables")))
        st.setdefault("auth", []).append(self.headers.get("Authorization"))
        st.setdefault("label_names", {"l-ready": "ai-ready"})
        st.setdefault("comments", [])
        data: dict | None
        if op == "ReadyIssues":
            f = v["filter"]
            want = f["labels"]["name"]["eq"]
            states = f["state"]["name"]["in"]
            team = ((f.get("team") or {}).get("key") or {}).get("eq")
            nodes = [i for i in st["issues"]
                     if any(l["name"] == want for l in i["labels"]["nodes"]) and i["state"]["name"] in states
                     and (not team or i["team"]["key"] == team)]
            data = {"issues": {"nodes": nodes}}
        elif op == "GetIssue":
            data = {"issue": self._issue(st, v["id"])}
        elif op == "IssueLabels":
            iss = self._issue(st, v["id"])
            data = {"issue": {"labels": iss["labels"]}}
        elif op == "IssueComments":
            nodes = [c for c in st["comments"] if c["issueId"] == v["id"]]
            data = {"issue": {"comments": {"nodes": nodes}}}
        elif op == "IssueState":
            if st.get("state_query_fails"):
                data = None
            else:
                n = st["state_polls"] = st.get("state_polls", 0) + 1
                after = st.get("cancel_after_polls")      # tras N consultas, alguien mueve la issue a Canceled
                name = "In Progress" if after is None or n <= after else st.get("cancel_state", "Canceled")
                data = {"issue": {"state": {"id": "s-x", "name": name, "type": "started"}}}
        elif op == "Viewer":
            data = {"viewer": {"id": "u-me", "name": "Yo", "email": "Me@x.com"}}
        elif op == "TeamStates":
            data = {"team": {"states": {"nodes": [
                {"id": "s-todo", "name": "Todo", "type": "unstarted"},
                {"id": "s-prog", "name": "In Progress", "type": "started"},
                {"id": "s-rev", "name": "In Review", "type": "started"}]}}}
        elif op == "TeamLabels":
            data = {"team": {"labels": {"nodes": [{"id": i, "name": n} for i, n in st["label_names"].items()]}}}
        elif op == "CreateLabel":
            name = v["input"]["name"]
            st["label_names"][label_id(name) if name != "ai-ready" else "l-ready"] = name
            data = {"issueLabelCreate": {"success": True, "issueLabel": {"id": label_id(name), "name": name}}}
        elif op == "UpdateIssue":
            iss = self._issue(st, v["id"]); inp = v["input"]
            if "stateId" in inp:
                name = STATE_NAMES[inp["stateId"]]
                iss["state"] = {"id": inp["stateId"], "name": name, "type": STATE_TYPES[name]}
            nodes = iss["labels"]["nodes"]
            for lid in inp.get("removedLabelIds", []):
                nodes[:] = [n for n in nodes if n["id"] != lid]
            for lid in inp.get("addedLabelIds", []):
                if all(n["id"] != lid for n in nodes):
                    nodes.append({"id": lid, "name": st["label_names"].get(lid, lid)})
            data = {"issueUpdate": {"success": True}}
        elif op == "AddComment":
            n = st["comment_seq"] = st.get("comment_seq", 0) + 1
            c = {"id": f"c-{n}", "issueId": v["input"]["issueId"], "body": v["input"]["body"],
                 "createdAt": f"2026-10-05T10:00:{n:02d}.000Z", "user": {"id": "u-me", "name": "Yo", "email": "me@x.com"}}
            st["comments"].append(c)
            data = {"commentCreate": {"success": True, "comment": {"id": c["id"], "createdAt": c["createdAt"]}}}
        else:
            data = {}
        out = json.dumps({"data": data} if data is not None else {"errors": [{"message": "simulado"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def human_comment(st, issue_id, body, user=None):
    """Simula que una persona comenta en la issue (despues de todo lo ya publicado por aipipe)."""
    n = st["comment_seq"] = st.get("comment_seq", 0) + 1
    st.setdefault("comments", []).append({
        "id": f"c-{n}", "issueId": issue_id, "body": body, "createdAt": f"2026-10-05T10:00:{n:02d}.000Z",
        "user": user or {"id": "u-me", "name": "Yo", "email": "me@x.com"}})


ME = {"id": "u-me", "name": "Yo", "email": "me@x.com"}
OTHER = {"id": "u-other", "name": "Otro", "email": "otro@x.com"}


def issue_node(identifier="ENG-1", title="Anadir feature", labels=("ai-ready",), priority=2, creator=ME):
    return {
        "creator": creator,
        "id": f"id-{identifier}", "identifier": identifier, "title": title,
        "description": "Crear feature.txt con la palabra done", "url": f"https://linear.app/x/issue/{identifier}",
        "priority": priority, "estimate": None,
        "state": {"id": "s-todo", "name": "Todo", "type": "unstarted"},
        "team": {"id": "team-1", "key": "ENG"},
        "labels": {"nodes": [{"id": label_id(n), "name": n} for n in labels]},
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AIPIPE_HOME", str(h))
    monkeypatch.setenv("AIPIPE_OPENCODE_HOME", str(tmp_path / "ocfg"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "agents.log"))
    monkeypatch.delenv("FAKE_MODE", raising=False)
    return h


@pytest.fixture
def repo(tmp_path, monkeypatch, home):
    r = tmp_path / "proj"
    r.mkdir()
    run = lambda *a: subprocess.run(a, cwd=r, check=True, capture_output=True, text=True)
    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (r / "README.md").write_text("hola\n")
    run("git", "add", "-A")
    run("git", "commit", "-m", "init")
    (r / ".aipipe.toml").write_text(
        '[linear]\nteam = "ENG"\n[project]\nbase_branch = "main"\npush = false\npr = false\n'
        'test_command = "python -c \\"import sys; sys.exit(0 if open(\'feature.txt\').read().strip()==\'done\' else 1)\\""\n'
        '[sandbox]\nmode = "off"\nenv_allow_extra = ["FAKE_*", "CHILD_PID_FILE"]\n'
    )
    monkeypatch.chdir(r)
    return r


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "opencode"
    exe.write_text(FAKE_OPENCODE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return exe


@pytest.fixture
def linear_server(monkeypatch):
    LinearMock.state = {"issues": [issue_node()]}
    srv = HTTPServer(("127.0.0.1", 0), LinearMock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    yield LinearMock.state, f"http://127.0.0.1:{srv.server_port}/graphql"
    srv.shutdown()


def agents_called(tmp_path) -> list[str]:
    log = tmp_path / "agents.log"
    return log.read_text().split() if log.exists() else []


@pytest.fixture(autouse=True)
def _sandbox_off_by_default(monkeypatch):
    """Los tests normales usan un OpenCode falso y directorios temporales: sin bwrap. test_sandbox.py lo activa."""
    monkeypatch.setenv("AIPIPE_SANDBOX", "off")
