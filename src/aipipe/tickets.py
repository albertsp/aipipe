"""Modelo de ticket y trackers (Linear real o local para pruebas sin red)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


APPROVE_RE = re.compile(r"^\W*(aprobad[oa]|aprobar|approved?|ok|lgtm|adelante|vale|dale|👍|✅)\W*$", re.I)
REJECT_RE = re.compile(r"^\W*(rechazad[oa]|rechazar|cancelad[oa]|cancelar|reject(?:ed)?|stop)\W*$", re.I)
CHANGES_RE = re.compile(r"^\s*(?:cambios|changes)\s*:\s*(\S.*)$", re.I | re.S)


@dataclass
class Decision:
    """Respuesta humana a un punto de control: approve | reject | changes (con el texto de los cambios)."""
    kind: str
    text: str = ""
    created_at: str = ""
    id: str = ""


def parse_decision(body: str) -> tuple[str, str] | None:
    """Solo cuenta el comentario ENTERO (`aprobado`, `cambios: ...`, `rechazado`): una frase suelta como
    "ok, luego lo miro" o el propio texto del plan, que contiene esas palabras, no aprueban nada."""
    body = (body or "").strip()
    if not body:
        return None
    if APPROVE_RE.match(body):
        return "approve", ""
    if REJECT_RE.match(body):
        return "reject", ""
    m = CHANGES_RE.match(body)
    if m:
        return "changes", m.group(1).strip()
    return None


@dataclass
class Ticket:
    identifier: str
    title: str
    description: str = ""
    url: str = ""
    labels: list[str] = field(default_factory=list)
    priority: int = 0
    estimate: float | None = None
    id: str = ""  # id interno del tracker
    team_id: str = ""
    team_key: str = ""
    creator: str = ""  # quien creo el ticket (email o nombre), solo informativo
    project: str = ""  # nombre del proyecto de Linear al que pertenece el ticket (solo informativo)

    def text(self) -> str:
        return f"{self.title}\n\n{self.description}".strip()


class Tracker(Protocol):
    def ready(self) -> list[Ticket]: ...
    def get(self, identifier: str) -> Ticket: ...
    def start(self, t: Ticket) -> None: ...
    def comment(self, t: Ticket, body: str) -> None: ...
    def finish(self, t: Ticket) -> None: ...
    def fail(self, t: Ticket) -> None: ...
    def release(self, t: Ticket) -> None: ...
    # Opcionales (el Runner usa getattr con valores por defecto si un tracker no los tiene):
    #   supports_cancel: bool       -> el tracker puede decir si alguien paro el ticket a mano
    #   is_cancelled(t) -> bool     -> True si la issue ya no esta "en progreso"
    #   abort(t) -> None            -> quita la etiqueta de disparo sin tocar el estado (evita reintentos en bucle)
    #   supports_approval: bool     -> permite puntos de control con aprobacion humana por comentario
    #   waiting() -> list[Ticket], wait(t), approval(t, since, exclude_ids) -> Decision | None, reject(t)
    #   set_phase(t, fase | None)   -> refleja la fase actual en la issue
    #   comment(t, body) puede devolver {"id", "createdAt"} del comentario creado


class LocalTracker:
    """Ticket desde un archivo markdown (`# Titulo` + cuerpo) o texto. Sin red; los comentarios van a stdout."""

    def __init__(self, ticket: Ticket, log=print):
        self.ticket = ticket
        self.log = log

    @classmethod
    def from_file(cls, path: Path, log=print) -> "LocalTracker":
        raw = path.read_text(encoding="utf-8")
        lines = raw.strip().splitlines()
        title = lines[0].lstrip("# ").strip() if lines else path.stem
        body = "\n".join(lines[1:]).strip()
        labels = re.findall(r"\bai:(?:light|std|standard|heavy)\b", raw)
        return cls(Ticket(identifier=path.stem.upper()[:24] or "LOCAL", title=title, description=body, labels=labels), log)

    def ready(self) -> list[Ticket]:
        return [self.ticket]

    def get(self, identifier: str) -> Ticket:
        return self.ticket

    def start(self, t: Ticket) -> None:
        self.log(f"[local] {t.identifier}: en progreso")

    def comment(self, t: Ticket, body: str) -> None:
        self.log(f"[local] comentario en {t.identifier}:\n{body}")

    def finish(self, t: Ticket) -> None:
        self.log(f"[local] {t.identifier}: en revision")

    def fail(self, t: Ticket) -> None:
        self.log(f"[local] {t.identifier}: fallido")

    def release(self, t: Ticket) -> None:
        self.log(f"[local] {t.identifier}: devuelto a la cola")

    supports_cancel = False

    def is_cancelled(self, t: Ticket) -> bool:
        return False

    def abort(self, t: Ticket) -> None:
        self.log(f"[local] {t.identifier}: detenido")

    supports_approval = False  # sin comentarios no hay a quien pedirle aprobacion: el flujo local sigue de corrido
