"""Router de modelos: reglas deterministas; el LLM de triage solo propone, las reglas deciden."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .budget import TIER_ORDER
from .pricing import TRAINS_ON_PROMPTS, model_key

LABEL_TIERS = {
    "ai:light": "light",
    "ai:std": "standard",
    "ai:standard": "standard",
    "ai:heavy": "heavy",
}

LIGHT_HINTS = re.compile(r"\b(typo|erratas?|readme|docs?|documentaci[oó]n|comment|comentarios?|rename|renombrar|bump|changelog|copy|texto)\b", re.I)
HEAVY_HINTS = re.compile(r"\b(refactor\w*|migra\w*|architect\w*|arquitectura|redise[nñ]\w*|rewrite|reescrib\w*|concurren\w*|seguridad|security|performance|rendimiento)\b", re.I)


@dataclass
class Route:
    tier: str
    reason: str
    needs_plan: bool


def parse_json_block(text: str) -> dict | None:
    """Extrae el primer objeto JSON de la salida de un modelo (tolera ```json y texto alrededor)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
        start = text.find("{", start + 1)
    for cand in candidates:
        try:
            value = json.loads(cand)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def bump(tier: str, n: int = 1) -> str:
    i = min(len(TIER_ORDER) - 1, TIER_ORDER.index(tier) + n)
    return TIER_ORDER[i]


def escalate(tier: str) -> str:
    return bump(tier, 1)


def heuristic_tier(text: str) -> tuple[str, str]:
    if HEAVY_HINTS.search(text):
        return "heavy", "palabras clave de tarea compleja"
    if LIGHT_HINTS.search(text) and len(text) < 600:
        return "light", "tarea trivial por palabras clave y longitud"
    return "standard", "tarea normal por defecto"


def route(labels: list[str], text: str, estimate: float | None, triage: dict | None) -> Route:
    # 1. etiqueta explicita ganadora
    for label in labels:
        tier = LABEL_TIERS.get(label.strip().lower())
        if tier:
            return Route(tier, f"etiqueta {label}", needs_plan=tier != "light")

    # 2. triage del modelo barato, con reglas encima
    if triage:
        try:
            complexity = max(1, min(5, int(triage.get("complexity", 3))))
        except (TypeError, ValueError):
            complexity = 3
        files = triage.get("files_estimate") or 0
        risk = str(triage.get("risk", "low")).lower()
        tier = "light" if complexity <= 1 else "standard" if complexity <= 3 else "heavy"
        notes = [f"complejidad {complexity}"]
        try:
            if int(files) >= 8:
                tier = bump(tier)
                notes.append(f"{files} archivos")
        except (TypeError, ValueError):
            pass
        if risk == "high":
            tier = bump(tier)
            notes.append("riesgo alto")
        if estimate is not None and estimate >= 8:
            tier = bump(tier)
            notes.append(f"estimacion {estimate:g}")
        needs_plan = bool(triage.get("needs_plan", tier != "light")) and tier != "light"
        return Route(tier, "triage: " + ", ".join(notes), needs_plan)

    # 3. sin triage: heuristica por texto
    tier, why = heuristic_tier(text)
    return Route(tier, f"heuristica: {why}", needs_plan=tier != "light")


def check_models(models: dict[str, str], allow_training: bool = False) -> list[str]:
    """Avisos sobre la tabla de modelos configurada."""
    issues = []
    for role, model in models.items():
        if not allow_training and model_key(model) in TRAINS_ON_PROMPTS:
            issues.append(f"El modelo del rol '{role}' ({model}) entrena con tus prompts: no lo uses con codigo privado.")
    return issues
