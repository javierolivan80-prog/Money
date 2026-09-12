"""guidance_detector.py — Fase 3: rellena has_prior_guidance/rumor_flag de novelty.py.

Hasta esta fase, NoveltyInputs.has_prior_guidance y .rumor_flag eran SIEMPRE
None (ver novelty.py — el score se calculaba solo con el componente de
drift de precio). Con filing_text.py ya extrayendo texto real, este módulo
cierra ese hueco con detección por PALABRAS CLAVE, no NLP semántico — mismo
principio de "regla antes que LLM" que edgar_scraper.py (Item de 8-K por
código, no por interpretación) y la misma honestidad sobre sus límites:
esto es un heurístico basto (falsos negativos si la empresa usa lenguaje no
estándar; falsos positivos si "guidance" aparece en un contexto no
relacionado), no un detector semántico. Se documenta como tal en vez de
aparentar más precisión de la que tiene.

DOS VENTANAS DISTINTAS a propósito:
  - Guidance: ventana larga (180 días) — una guía de resultados dada a
    principios de año sigue siendo relevante para el evento de fin de año.
  - Rumor: ventana corta (30 días) — la rumorología pierde relevancia rápido;
    un rumor de hace 4 meses no anticipa el evento de hoy.

AUSENCIA DE DATO != AUSENCIA DE SEÑAL: si no hay ningún filing_text
disponible en la ventana (porque no hubo filings previos, o porque el
backfill de texto de filing_text.py aún no los cubre), la función devuelve
None, no False — igual que el resto de NoveltyInputs. Devolver False
significaría afirmar "no hubo guidance/rumor" cuando en realidad no se pudo
mirar, y eso inflaría artificialmente el novelty_score.
"""
from __future__ import annotations

from datetime import date, timedelta

GUIDANCE_LOOKBACK_DAYS = 180
RUMOR_LOOKBACK_DAYS = 30
DEFAULT_LIMIT = 10

GUIDANCE_KEYWORDS = [
    "guidance", "outlook", "we expect", "we anticipate", "reaffirm",
    "raising our", "raising its", "lowering our", "lowering its",
    "full-year outlook", "full year outlook", "forecast",
]

RUMOR_KEYWORDS = [
    "rumored", "rumoured", "reportedly", "sources familiar", "sources say",
    "media reports", "according to people familiar", "speculation",
    "expected to announce", "in talks", "under consideration",
]


def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in keywords)


def detect_guidance(prior_texts: list[str]) -> bool:
    """Pura — recibe una lista YA recuperada de textos de filings previos."""
    return any(_contains_any(t, GUIDANCE_KEYWORDS) for t in prior_texts if t)


def detect_rumor(prior_texts: list[str]) -> bool:
    return any(_contains_any(t, RUMOR_KEYWORDS) for t in prior_texts if t)


def get_prior_filing_texts(conn, ticker: str, before_date: date, lookback_days: int, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Textos de filings del mismo ticker, ESTRICTAMENTE anteriores a
    before_date (anti-look-ahead — misma disciplina que
    historical_analogues.py: nunca se usa información que no existía todavía
    en el momento simulado de la decisión), dentro de la ventana de lookback."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT filing_text FROM events
            WHERE ticker = %(ticker)s
              AND d0_close_date < %(before_date)s
              AND d0_close_date >= %(window_start)s
              AND filing_text IS NOT NULL
            ORDER BY d0_close_date DESC
            LIMIT %(limit)s
            """,
            {
                "ticker": ticker,
                "before_date": before_date,
                "window_start": before_date - timedelta(days=lookback_days),
                "limit": limit,
            },
        )
        return [r["filing_text"] for r in cur.fetchall()]


def compute_novelty_signals(conn, ticker: str, as_of_date: date) -> tuple[bool | None, bool | None]:
    """Punto de entrada usado por el orquestador. Devuelve
    (has_prior_guidance, rumor_flag) — cada uno None si no hay texto
    disponible en su ventana correspondiente (ver docstring del módulo)."""
    guidance_texts = get_prior_filing_texts(conn, ticker, as_of_date, GUIDANCE_LOOKBACK_DAYS)
    rumor_texts = get_prior_filing_texts(conn, ticker, as_of_date, RUMOR_LOOKBACK_DAYS)

    has_guidance = detect_guidance(guidance_texts) if guidance_texts else None
    has_rumor = detect_rumor(rumor_texts) if rumor_texts else None
    return has_guidance, has_rumor
