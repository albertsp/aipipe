"""Libro de gasto local (JSONL). Solo ve lo que ejecuta aipipe; el uso manual se anota con `aipipe budget --add-usd`."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import ledger_path


@dataclass
class Entry:
    ts: datetime
    issue: str
    role: str
    model: str
    requests: int
    tokens: dict
    cost_usd: float
    normalized: float  # cost / limite mensual del modelo: fraccion del cupo mensual consumida

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts.astimezone(timezone.utc).isoformat(),
                "issue": self.issue,
                "role": self.role,
                "model": self.model,
                "requests": self.requests,
                "tokens": self.tokens,
                "cost_usd": round(self.cost_usd, 6),
                "normalized": round(self.normalized, 8),
            }
        )


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = path or ledger_path()

    def append(self, entry: Entry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.to_json() + "\n")

    def read(self, since: datetime | None = None) -> list[Entry]:
        if not self.path.exists():
            return []
        out: list[Entry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                ts = datetime.fromisoformat(raw["ts"])
            except (ValueError, KeyError):
                continue
            if since and ts < since:
                continue
            out.append(
                Entry(
                    ts=ts,
                    issue=raw.get("issue", ""),
                    role=raw.get("role", ""),
                    model=raw.get("model", ""),
                    requests=int(raw.get("requests", 0)),
                    tokens=raw.get("tokens", {}),
                    cost_usd=float(raw.get("cost_usd", 0)),
                    normalized=float(raw.get("normalized", 0)),
                )
            )
        return out
