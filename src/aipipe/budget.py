"""Guardia de presupuesto.

Lectura conservadora de los limites de Go: cada modelo consume `coste / limite_mensual_del_modelo` del cupo
(un modelo de 15$ gasta 4x mas cupo por dolar que uno de 60$). Se suman esas fracciones por ventana y se
comparan con 20% (5h), 50% (semana) y 100% (mes). Si la doc se interpretara como un pozo comun de 60$ con
topes por modelo, esta suma sigue siendo un limite superior valido.

La garantia dura de no pasar de 10$ es dejar desactivado "Use balance" en la consola de Go.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .ledger import Entry, Ledger
from .pricing import Tokens, estimate_cost, price_for

WINDOWS = {
    "5h": (timedelta(hours=5), 0.20),
    "week": (timedelta(days=7), 0.50),
    "month": (timedelta(days=30), 1.00),
}

TIER_ORDER = ["light", "standard", "heavy"]


@dataclass
class Decision:
    action: str  # ok | downgrade | pause
    tier: str
    reason: str = ""
    resume_at: datetime | None = None


def fractions(entries: list[Entry], now: datetime) -> dict[str, float]:
    out = {}
    for name, (span, cap) in WINDOWS.items():
        used = sum(e.normalized for e in entries if e.ts >= now - span)
        out[name] = used / cap
    return out


class Budget:
    def __init__(self, cfg: dict, ledger: Ledger | None = None):
        self.cfg = cfg
        self.ledger = ledger or Ledger()

    # --- registro -------------------------------------------------------
    def record(self, issue: str, role: str, model: str, tokens: Tokens, when: datetime | None = None) -> Entry:
        when = when or datetime.now(timezone.utc)
        overrides = self.cfg.get("pricing") or {}
        price, _ = price_for(model, overrides)
        cost = estimate_cost(model, tokens, when, overrides)
        limit = price.limit if price.limit not in (0, float("inf")) else None
        normalized = (cost / limit) if limit else 0.0
        entry = Entry(
            ts=when,
            issue=issue,
            role=role,
            model=model,
            requests=tokens.requests,
            tokens={
                "input": tokens.input,
                "output": tokens.output,
                "reasoning": tokens.reasoning,
                "cache_read": tokens.cache_read,
                "cache_write": tokens.cache_write,
            },
            cost_usd=cost,
            normalized=normalized,
        )
        self.ledger.append(entry)
        return entry

    def add_external_usd(self, usd: float, note: str = "external") -> Entry:
        """Uso manual fuera de aipipe (p. ej. OpenCode a mano). Se normaliza contra 60$ (el modelo mas holgado)."""
        entry = Entry(
            ts=datetime.now(timezone.utc),
            issue=note,
            role="external",
            model="external",
            requests=0,
            tokens={},
            cost_usd=usd,
            normalized=usd / 60.0,
        )
        self.ledger.append(entry)
        return entry

    # --- consulta -------------------------------------------------------
    def status(self, now: datetime | None = None) -> dict[str, float]:
        now = now or datetime.now(timezone.utc)
        entries = self.ledger.read(since=now - WINDOWS["month"][0])
        return fractions(entries, now)

    def spend_usd(self, now: datetime | None = None) -> dict[str, float]:
        now = now or datetime.now(timezone.utc)
        entries = self.ledger.read(since=now - WINDOWS["month"][0])
        return {n: sum(e.cost_usd for e in entries if e.ts >= now - span) for n, (span, _) in WINDOWS.items()}

    def _resume_at(self, entries: list[Entry], now: datetime, limit: float) -> datetime | None:
        """Instante en que todas las ventanas vuelven por debajo de `limit` al caducar las entradas mas antiguas."""
        latest = now
        for name, (span, cap) in WINDOWS.items():
            live = sorted((e for e in entries if e.ts >= now - span), key=lambda e: e.ts)
            used = sum(e.normalized for e in live)
            if used / cap < limit:
                continue
            for e in live:
                used -= e.normalized
                if used / cap < limit:
                    latest = max(latest, e.ts + span)
                    break
            else:
                latest = max(latest, now + span)
        return latest

    def decide(self, tier: str, now: datetime | None = None) -> Decision:
        now = now or datetime.now(timezone.utc)
        b = self.cfg["budget"]
        entries = self.ledger.read(since=now - WINDOWS["month"][0])
        fr = fractions(entries, now)
        worst_name = max(fr, key=fr.get)
        worst = fr[worst_name]
        if worst >= b["soft_stop"]:
            return Decision(
                "pause",
                tier,
                f"ventana {worst_name} al {worst:.0%} del cupo (limite blando {b['soft_stop']:.0%})",
                resume_at=self._resume_at(entries, now, b["soft_stop"]),
            )
        new_tier = tier
        reason = ""
        if tier == "heavy" and worst >= b["heavy_stop"]:
            new_tier = "standard"
            reason = f"ventana {worst_name} al {worst:.0%}: heavy no permitido por encima de {b['heavy_stop']:.0%}"
        if new_tier == "standard" and worst >= b["standard_stop"]:
            new_tier = "light"
            reason = f"ventana {worst_name} al {worst:.0%}: standard no permitido por encima de {b['standard_stop']:.0%}"
        if new_tier != tier:
            return Decision("downgrade", new_tier, reason)
        return Decision("ok", tier)
