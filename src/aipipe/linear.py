"""Cliente minimo de Linear (GraphQL, solo libreria estandar). Nada de LLM: todo el I/O con Linear es determinista."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from .tickets import Decision, Ticket, parse_decision

PHASES = ("plan", "impl", "review")


class LinearError(RuntimeError):
    pass


FRAGMENT = """
fragment IssueFields on Issue {
  id identifier title description url priority estimate
  state { id name type }
  team { id key }
  creator { id name email }
  labels { nodes { id name } }
}
"""

Q_READY = (
    "query ReadyIssues($filter: IssueFilter, $first: Int) {"
    " issues(filter: $filter, first: $first) { nodes { ...IssueFields } } }" + FRAGMENT
)
Q_GET = "query GetIssue($id: String!) { issue(id: $id) { ...IssueFields } }" + FRAGMENT
Q_VIEWER = "query Viewer { viewer { id name email } }"
Q_STATE = "query IssueState($id: String!) { issue(id: $id) { state { id name type } } }"
Q_STATES = "query TeamStates($teamId: String!) { team(id: $teamId) { states { nodes { id name type } } } }"
Q_LABELS = "query TeamLabels($teamId: String!) { team(id: $teamId) { labels { nodes { id name } } } }"
M_UPDATE = "mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }"
M_COMMENT = (
    "mutation AddComment($input: CommentCreateInput!) {"
    " commentCreate(input: $input) { success comment { id createdAt } } }"
)
Q_ISSUE_LABELS = "query IssueLabels($id: String!) { issue(id: $id) { labels { nodes { id name } } } }"
Q_COMMENTS = (
    "query IssueComments($id: String!) { issue(id: $id) {"
    " comments(first: 100) { nodes { id body createdAt user { id name email } } } } }"
)
M_LABEL = (
    "mutation CreateLabel($input: IssueLabelCreateInput!) {"
    " issueLabelCreate(input: $input) { success issueLabel { id name } } }"
)


def _ticket(node: dict) -> Ticket:
    return Ticket(
        id=node["id"],
        identifier=node["identifier"],
        title=node.get("title") or "",
        description=node.get("description") or "",
        url=node.get("url") or "",
        labels=[n["name"] for n in (node.get("labels") or {}).get("nodes", [])],
        priority=node.get("priority") or 0,
        estimate=node.get("estimate"),
        team_id=(node.get("team") or {}).get("id", ""),
        team_key=(node.get("team") or {}).get("key", ""),
        creator=_creator_label(node),
    )


def _creator_label(node: dict) -> str:
    c = node.get("creator") or {}
    return c.get("email") or c.get("name") or ""


class LinearTracker:
    def __init__(self, cfg: dict, log=print):
        lin = cfg["linear"]
        self.cfg = lin
        self.log = log
        key = os.environ.get(lin["api_key_env"], "")
        if not key:
            raise LinearError(f"Falta la variable de entorno {lin['api_key_env']} (API key personal de Linear)")
        self.key = key
        self._states: dict[str, dict[str, str]] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._allowed: set[str] | None = None
        self._viewer_id: str | None = None  # solo en modo por defecto (dueño de la clave)
        self._skipped: set[str] = set()

    # --- transporte -----------------------------------------------------
    def _gql(self, query: str, variables: dict | None = None, op: str | None = None, retries: int = 3) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}, "operationName": op}).encode()
        req = urllib.request.Request(
            self.cfg["api_url"],
            data=body,
            headers={"Content-Type": "application/json", "Authorization": self.key},
        )
        last: Exception | None = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode())
                if payload.get("errors"):
                    raise LinearError(f"Linear devolvio errores: {payload['errors']}")
                return payload["data"]
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                last = LinearError(f"HTTP {exc.code} de Linear: {detail}")
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError) as exc:
                last = LinearError(f"No se pudo contactar con Linear: {exc}")
            time.sleep(1.5 * (attempt + 1))
        raise last or LinearError("error desconocido con Linear")

    # --- cache de estados/etiquetas --------------------------------------
    def _state_id(self, team_id: str, name: str) -> str:
        if team_id not in self._states:
            data = self._gql(Q_STATES, {"teamId": team_id}, "TeamStates")
            self._states[team_id] = {s["name"].lower(): s["id"] for s in data["team"]["states"]["nodes"]}
        try:
            return self._states[team_id][name.lower()]
        except KeyError:
            raise LinearError(
                f"El estado '{name}' no existe en el equipo. Disponibles: {sorted(self._states[team_id])}"
            ) from None

    def _label_id(self, team_id: str, name: str, create: bool = True) -> str | None:
        if team_id not in self._labels:
            data = self._gql(Q_LABELS, {"teamId": team_id}, "TeamLabels")
            self._labels[team_id] = {n["name"].lower(): n["id"] for n in data["team"]["labels"]["nodes"]}
        found = self._labels[team_id].get(name.lower())
        if found or not create:
            return found
        data = self._gql(M_LABEL, {"input": {"name": name, "teamId": team_id}}, "CreateLabel")
        label = data["issueLabelCreate"]["issueLabel"]
        self._labels[team_id][name.lower()] = label["id"]
        return label["id"]

    # --- quien puede disparar al agente ----------------------------------
    def _allowed_creators(self) -> set[str]:
        """Ids/emails (en minusculas) cuyos tickets se ejecutan. Por defecto: solo el dueño de la API key."""
        if self._allowed is None:
            explicit = {str(x).strip().lower() for x in (self.cfg.get("allowed_creators") or []) if str(x).strip()}
            if explicit:
                self._allowed = explicit
            else:
                viewer = self._gql(Q_VIEWER, None, "Viewer").get("viewer") or {}
                ids = {str(viewer[k]).lower() for k in ("id", "email") if viewer.get(k)}
                if not ids:
                    raise LinearError(
                        "No pude identificar al dueño de la API key para filtrar por creador. "
                        "Define linear.allowed_creators en .aipipe.toml."
                    )
                self._allowed = ids
                self._viewer_id = viewer.get("id")
        return self._allowed

    def _authorized(self, node: dict) -> bool:
        if self.cfg.get("allow_any_creator"):
            return True
        c = node.get("creator") or {}  # creador desconocido (integracion, usuario borrado) -> se rechaza
        mine = {str(c[k]).lower() for k in ("id", "email") if c.get(k)}
        return bool(mine & self._allowed_creators())

    def _reject_msg(self, node: dict) -> str:
        who = _creator_label(node) or "desconocido"
        return (
            f"{node.get('identifier')} no se ejecuta: lo creo '{who}', que no esta autorizado. aipipe solo ejecuta issues "
            "del dueño de la API key (o de linear.allowed_creators), porque su texto llega a un agente con shell."
        )

    # --- Tracker ---------------------------------------------------------
    def ready(self) -> list[Ticket]:
        return self._query_issues(self.cfg["trigger_label"], self.cfg["trigger_states"])

    def waiting(self) -> list[Ticket]:
        """Issues que esperan una aprobacion humana (etiqueta `waiting_label`, en curso)."""
        return self._query_issues(self.cfg["waiting_label"], [self.cfg["state_in_progress"]])

    def _query_issues(self, label: str, states: list[str]) -> list[Ticket]:
        filt: dict = {
            "labels": {"name": {"eq": label}},
            "state": {"name": {"in": list(states)}},
        }
        if self.cfg["team"]:
            filt["team"] = {"key": {"eq": self.cfg["team"]}}
        check = not self.cfg.get("allow_any_creator")
        if check:
            self._allowed_creators()
            if self._viewer_id:  # modo por defecto: Linear ya descarta lo ajeno y no ocupa hueco en la pagina
                filt["creator"] = {"id": {"eq": self._viewer_id}}
        data = self._gql(Q_READY, {"filter": filt, "first": int(self.cfg["max_per_batch"]) * 3}, "ReadyIssues")
        tickets = []
        for node in data["issues"]["nodes"]:
            if check and not self._authorized(node):  # defensa en profundidad: la comprobacion local es la autoritativa
                if node.get("identifier") not in self._skipped:
                    self._skipped.add(node.get("identifier"))
                    self.log(f"AVISO: {self._reject_msg(node)}")
                continue
            tickets.append(_ticket(node))
        # prioridad de Linear: 1 urgente ... 4 baja; 0 = sin prioridad (va al final)
        tickets.sort(key=lambda t: (t.priority == 0, t.priority))
        return tickets

    def get(self, identifier: str) -> Ticket:
        data = self._gql(Q_GET, {"id": identifier}, "GetIssue")
        if not data.get("issue"):
            raise LinearError(f"No existe el ticket {identifier}")
        if not self._authorized(data["issue"]):
            raise LinearError(self._reject_msg(data["issue"]))
        return _ticket(data["issue"])

    def _update(self, t: Ticket, **input_) -> None:
        self._gql(M_UPDATE, {"id": t.id, "input": input_}, "UpdateIssue")

    def _relabel(self, t: Ticket, add=(), remove=(), **fields) -> None:
        """Un solo issueUpdate que cambia etiquetas (y opcionalmente estado). Solo quita las que la issue TIENE ahora
        (se consultan): no se manda a Linear quitar una etiqueta que no esta puesta. Las etiquetas por crear se crean."""
        data = self._gql(Q_ISSUE_LABELS, {"id": t.id}, "IssueLabels")
        current = {n["name"].lower(): n["id"] for n in ((data.get("issue") or {}).get("labels") or {}).get("nodes", [])}
        input_: dict = dict(fields)
        added = [self._label_id(t.team_id, n) for n in add if n.lower() not in current]
        removed = [current[n.lower()] for n in remove if n.lower() in current]
        if added:
            input_["addedLabelIds"] = added
        if removed:
            input_["removedLabelIds"] = removed
        if input_:
            self._update(t, **input_)

    def _phase_names(self) -> list[str]:
        return [self.cfg["phase_prefix"] + p for p in PHASES]

    def set_phase(self, t: Ticket, phase: str | None) -> None:
        """Refleja la fase actual (plan / impl / review) con UNA etiqueta; None las quita todas."""
        if not self.cfg.get("phase_labels", True):
            return
        names = self._phase_names()
        keep = self.cfg["phase_prefix"] + phase if phase else None
        self._relabel(t, add=[keep] if keep else [], remove=[n for n in names if n != keep])

    def start(self, t: Ticket) -> None:
        self._update(t, stateId=self._state_id(t.team_id, self.cfg["state_in_progress"]))

    def comment(self, t: Ticket, body: str) -> dict:
        data = self._gql(M_COMMENT, {"input": {"issueId": t.id, "body": body}}, "AddComment")
        return ((data.get("commentCreate") or {}).get("comment")) or {}

    def finish(self, t: Ticket) -> None:
        self._relabel(
            t, remove=[self.cfg["trigger_label"], self.cfg["waiting_label"], *self._phase_names()],
            stateId=self._state_id(t.team_id, self.cfg["state_in_review"]),
        )

    def fail(self, t: Ticket) -> None:
        self._relabel(
            t, add=[self.cfg["failed_label"]],
            remove=[self.cfg["trigger_label"], self.cfg["waiting_label"], *self._phase_names()],
            stateId=self._state_id(t.team_id, self.cfg["trigger_states"][0]),
        )

    def release(self, t: Ticket) -> None:
        self._relabel(
            t, remove=[self.cfg["waiting_label"], *self._phase_names()],
            stateId=self._state_id(t.team_id, self.cfg["trigger_states"][0]),
        )

    # --- cancelacion -------------------------------------------------------
    supports_cancel = True

    def is_cancelled(self, t: Ticket) -> bool:
        """True si alguien movio la issue fuera de `state_in_progress` (Canceled, Done, Backlog, Todo...) mientras
        aipipe trabajaba: se interpreta como "para". Un fallo de red NUNCA cuenta como cancelacion."""
        try:
            data = self._gql(Q_STATE, {"id": t.id}, "IssueState", retries=1)
        except LinearError:
            return False
        state = (data.get("issue") or {}).get("state") or {}
        name = state.get("name") or ""
        return bool(name) and name.lower() != self.cfg["state_in_progress"].lower()

    def abort(self, t: Ticket) -> None:
        """Tras una cancelacion: quita etiquetas de disparo/espera/fase y NO toca el estado (lo puso el usuario). Sin
        esto, una issue devuelta a Todo con `ai-ready` se volveria a ejecutar sola en el siguiente sondeo."""
        self._relabel(t, remove=[self.cfg["trigger_label"], self.cfg["waiting_label"], *self._phase_names()])

    # --- puntos de control con aprobacion por comentario ---------------------
    supports_approval = True

    def wait(self, t: Ticket) -> None:
        """Pasa la issue a 'esperando': quita la etiqueta de disparo (no se vuelve a coger como trabajo nuevo) y pone la
        de espera. El estado sigue en 'In Progress'. No hay ningun proceso ni gasto mientras tanto."""
        self._relabel(t, add=[self.cfg["waiting_label"]], remove=[self.cfg["trigger_label"], *self._phase_names()])

    def reject(self, t: Ticket) -> None:
        """Plan rechazado: vuelve a Todo SIN etiqueta de disparo (no se relanza sola)."""
        self._relabel(
            t, remove=[self.cfg["waiting_label"], self.cfg["trigger_label"], *self._phase_names()],
            stateId=self._state_id(t.team_id, self.cfg["trigger_states"][0]),
        )

    def approval(self, t: Ticket, since: str, exclude_ids=()) -> Decision | None:
        """Primera respuesta decisiva (aprobado / cambios: ... / rechazado) posterior a `since`, escrita por alguien
        autorizado. Los comentarios de terceros se ignoran: aprobar equivale a mandar ejecutar codigo."""
        data = self._gql(Q_COMMENTS, {"id": t.id}, "IssueComments")
        nodes = ((data.get("issue") or {}).get("comments") or {}).get("nodes", [])
        for n in sorted(nodes, key=lambda n: n.get("createdAt") or ""):
            if (n.get("createdAt") or "") <= since or n.get("id") in set(exclude_ids):
                continue
            if not self._authorized({"creator": n.get("user")}):
                continue
            parsed = parse_decision(n.get("body") or "")
            if parsed:
                return Decision(parsed[0], parsed[1], n["createdAt"], n.get("id", ""))
        return None
