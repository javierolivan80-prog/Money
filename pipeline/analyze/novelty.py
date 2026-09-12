"""novelty.py — Etapa 2: ¿qué tan sorpresa fue esto?

De las 4 preguntas que pide el spec para construir el score, SOLO UNA es
computable hoy sin más infraestructura: el movimiento de precio D-5→D-1 (viene
directo de prices, ya en la base de datos). Las otras tres —¿había guidance
previo?, ¿lo esperaban los analistas? (proxy: menciones en 10-Q previo),
¿hay rumoración anterior en EDGAR? (buscar "expected"+keyword)— requieren
BUSCAR TEXTO DENTRO de filings anteriores. edgar_scraper.py (Fase 1) guarda
la URL y los metadatos de cada filing, pero NUNCA descarga ni indexa el texto
completo (ver RUNBOOK.md, gap ya documentado en la Fase 1: "extracción del
texto real del filing"). Sin eso, has_prior_guidance y rumor_flag no se
pueden calcular — no porque la lógica no exista, sino porque no hay de dónde
leerlos todavía.

Esta Etapa 2 se construye por tanto para aceptar CUALQUIER subconjunto de las
tres señales — la de precio siempre, guidance/rumor como None cuando faltan
— y pondera solo con las señales disponibles, renormalizando pesos, en vez de
fingir neutralidad (un None tratado como "neutral" sesgaría el score hacia el
centro exactamente en los eventos donde falta más información, que es lo
opuesto de lo que se quiere). El score de HOY, en la práctica, es
esencialmente el componente de precio hasta que se construya el extractor de
texto de filings — eso queda explícito en NoveltyResult.reasoning
(`components_used` / `components_unavailable`), no oculto.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Movimiento pre-evento (D-5 -> D-1, en % absoluto) a partir del cual se
# considera "completamente anticipado" (score de este componente = 0).
DRIFT_SATURATION_PCT = 8.0

_WEIGHTS = {
    "drift": 0.50,
    "guidance": 0.25,
    "rumor": 0.25,
}


@dataclass
class NoveltyInputs:
    pre_event_drift_pct: float          # siempre disponible (de event_enrichment)
    has_prior_guidance: bool | None = None  # None = no calculable hoy (ver docstring)
    rumor_flag: bool | None = None          # None = no calculable hoy


@dataclass
class NoveltyResult:
    score: float  # 0-100, 100 = sorpresa extrema
    components_used: list[str] = field(default_factory=list)
    components_unavailable: list[str] = field(default_factory=list)
    reasoning: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {
            "score": round(self.score, 1),
            "components_used": self.components_used,
            "components_unavailable": self.components_unavailable,
            **self.reasoning,
        }


def _drift_component(drift_pct: float) -> float:
    return max(0.0, min(100.0, 100.0 - (abs(drift_pct) / DRIFT_SATURATION_PCT) * 100.0))


def compute_novelty(inputs: NoveltyInputs) -> NoveltyResult:
    components: dict[str, float] = {"drift": _drift_component(inputs.pre_event_drift_pct)}
    used = ["drift"]
    unavailable = []

    if inputs.has_prior_guidance is None:
        unavailable.append("has_prior_guidance")
    else:
        # Guidance previo => más anticipado => score bajo en este componente.
        components["guidance"] = 20.0 if inputs.has_prior_guidance else 100.0
        used.append("guidance")

    if inputs.rumor_flag is None:
        unavailable.append("rumor_flag")
    else:
        components["rumor"] = 10.0 if inputs.rumor_flag else 100.0
        used.append("rumor")

    total_weight = sum(_WEIGHTS[k] for k in components)
    score = sum(components[k] * _WEIGHTS[k] for k in components) / total_weight

    return NoveltyResult(
        score=score,
        components_used=used,
        components_unavailable=unavailable,
        reasoning={
            "pre_event_drift_pct": inputs.pre_event_drift_pct,
            "has_prior_guidance": inputs.has_prior_guidance,
            "rumor_flag": inputs.rumor_flag,
            "note_on_unavailable": (
                "has_prior_guidance/rumor_flag requieren texto de filings previos, "
                "no implementado aún (ver RUNBOOK.md) — el score se calcula solo con "
                "las señales disponibles, sin asumir neutralidad para las que faltan."
                if unavailable
                else None
            ),
        },
    )
