"""Precios por 1M de tokens y limite mensual por modelo, segun opencode.ai/docs/go (3 oct 2026).

Son una estimacion local: la cifra autoritativa es la consola de Go. Para modelos con precio por tramo
o por hora pico se usa el precio mas alto (conservador); DeepSeek en valle cuesta la mitad.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Price:
    inp: float
    out: float
    cache_read: float
    cache_write: float  # 0 = sin precio propio: se factura como input
    limit: float  # limite mensual en $ del modelo dentro de Go (inf = ilimitado)


INF = float("inf")

PRICES: dict[str, Price] = {
    "glm-5.3-flash": Price(0.15, 0.50, 0.03, 0, 60),
    "glm-5.3": Price(1.40, 4.40, 0.26, 0, 15),
    "glm-5.2": Price(1.40, 4.40, 0.26, 0, 60),
    "kimi-k3": Price(3.00, 15.00, 0.30, 0, 15),
    "kimi-k2.7-code": Price(0.95, 4.00, 0.19, 0, 60),
    "kimi-k2.6": Price(0.95, 4.00, 0.16, 0, 60),
    "longcat-2.0": Price(0.30, 1.20, 0.006, 0, 60),
    "longcat-2.5-preview-free": Price(0, 0, 0, 0, INF),
    "mimo-v2.6-flash": Price(0.14, 0.28, 0.0028, 0, 60),
    "mimo-v2.6-pro": Price(0.435, 0.87, 0.003625, 0, 15),
    "mimo-v2.5": Price(0.14, 0.28, 0.0028, 0, 60),
    "mimo-v2.5-pro": Price(0.435, 0.87, 0.003625, 0, 15),
    "minimax-m3": Price(0.30, 1.20, 0.06, 0, 60),
    "minimax-m2.7": Price(0.30, 1.20, 0.06, 0.375, 60),
    "muse-spark-1.3-contributor": Price(0.10, 0.20, 0.002, 0, 60),
    "muse-spark-1.2-contributor": Price(0.10, 0.20, 0.002, 0, 60),
    "qwen3.8-max": Price(2.00, 6.00, 0.25, 2.50, 15),
    "qwen3.8-flash": Price(0.15, 0.47, 0.016, 0.20, 30),
    "qwen3.7-plus": Price(1.20, 4.80, 0.12, 1.50, 60),
    "deepseek-v4.1-flash": Price(0.30, 1.20, 0.006, 0, 60),
    "deepseek-v4-pro": Price(1.32, 3.96, 0.044, 0, 15),
    "deepseek-v4-flash": Price(0.30, 1.20, 0.006, 0, 30),
    "deepseek-v4-flash-vision-exp": Price(0.30, 1.20, 0.006, 0, 15),
    "hy4-preview": Price(0.834, 2.501, 0.042, 0, 30),
    "hy3": Price(0.14, 0.58, 0.035, 0, 60),
    "space-bunny-free": Price(0, 0, 0, 0, INF),
    "grok-4.7": Price(4.00, 12.00, 1.00, 0, 15),
    "grok-4.6": Price(4.00, 12.00, 1.00, 0, 15),
    "gpt-6-luna": Price(0.20, 0.75, 0.02, 0.25, 15),
    "gpt-5.6-luna": Price(0.40, 1.80, 0.04, 0.50, 15),
}

# Desconocido: asumir caro y con tope bajo (peor caso) para no subestimar el gasto.
UNKNOWN = Price(3.00, 15.00, 0.30, 0, 15)

# Modelos que entrenan con tus prompts (privacidad): el router los rechaza por defecto.
TRAINS_ON_PROMPTS = {"muse-spark-1.3-contributor", "muse-spark-1.2-contributor"}


@dataclass
class Tokens:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0
    requests: int = 0

    def add(self, other: "Tokens") -> None:
        for name in ("input", "output", "reasoning", "cache_read", "cache_write", "requests"):
            setattr(self, name, getattr(self, name) + getattr(other, name))


def model_key(model: str) -> str:
    return model.split("/", 1)[-1].strip().lower()


def price_for(model: str, overrides: dict | None = None) -> tuple[Price, bool]:
    key = model_key(model)
    base = PRICES.get(key)
    known = base is not None
    base = base or UNKNOWN
    ov = (overrides or {}).get(key) or (overrides or {}).get(model)
    if ov:
        base = Price(
            float(ov.get("input", base.inp)),
            float(ov.get("output", base.out)),
            float(ov.get("cache_read", base.cache_read)),
            float(ov.get("cache_write", base.cache_write)),
            float(ov.get("limit", base.limit)),
        )
        known = True
    return base, known


def deepseek_off_peak(when: datetime) -> bool:
    """Pico DeepSeek: 01-04 y 06-10 UTC, lunes a viernes. Fuera de eso, mitad de precio."""
    when = when.astimezone(timezone.utc)
    if when.weekday() >= 5:
        return True
    return not (1 <= when.hour < 4 or 6 <= when.hour < 10)


def estimate_cost(model: str, tokens: Tokens, when: datetime, overrides: dict | None = None) -> float:
    price, _ = price_for(model, overrides)
    cw_price = price.cache_write or price.inp
    cost = (
        tokens.input * price.inp
        + (tokens.output + tokens.reasoning) * price.out
        + tokens.cache_read * price.cache_read
        + tokens.cache_write * cw_price
    ) / 1_000_000
    if model_key(model).startswith("deepseek") and deepseek_off_peak(when):
        cost *= 0.5
    return cost
