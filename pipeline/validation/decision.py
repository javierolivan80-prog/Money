"""decision.py — Fase 6 PARTE 6 ("DECISIÓN INVERSIÓN"): las 3 opciones
literales del spec (GREENLIGHT/YELLOWLIGHT/REDLIGHT), con los umbrales
exactos que da el spec. Puro (recibe números ya extraídos, no conn) para
poder probarse con fixtures a mano, mismo patrón que
portfolio_report.py:generate_recommendation."""
from __future__ import annotations

GREENLIGHT_MIN_WIN_RATE = 0.55
GREENLIGHT_MIN_SHARPE = 1.0
GREENLIGHT_MIN_CALIBRATION = 0.6
GREENLIGHT_MAX_DRAWDOWN = 0.15
GREENLIGHT_MIN_TRADES = 300

REDLIGHT_MAX_SHARPE = 0.7
REDLIGHT_MIN_WIN_RATE = 0.50
REDLIGHT_MIN_CALIBRATION = 0.4


def generate_decision(
    win_rate: float | None,
    sharpe: float | None,
    calibration_score: float | None,
    max_drawdown: float | None,
    n_trades: int,
    walk_forward_passed: bool | None,
    no_lookahead_violations: list[str],
) -> dict:
    """Evalúa las condiciones de OPTION A (GREENLIGHT) primero, luego las de
    OPTION C (REDLIGHT) — cualquier caso que no sea ni A ni C es OPTION B
    (YELLOWLIGHT), literal del spec ("mejor que OPTION C, pero algunos
    flags"). Datos faltantes (None) nunca cuentan como si pasaran un
    umbral — None es "no lo sabemos", no "sí"."""
    reasons: list[str] = []

    if no_lookahead_violations:
        reasons.append(f"{len(no_lookahead_violations)} violación(es) anti-look-ahead detectadas — bloqueante para cualquier decisión positiva")
        return {
            "option": "C",
            "label": "REDLIGHT",
            "recommendation": "Revisita con más datos o pivotar estrategia. No apto para capital real hoy.",
            "reasons": reasons,
        }

    passes_greenlight = (
        win_rate is not None and win_rate > GREENLIGHT_MIN_WIN_RATE
        and sharpe is not None and sharpe > GREENLIGHT_MIN_SHARPE
        and calibration_score is not None and calibration_score > GREENLIGHT_MIN_CALIBRATION
        and max_drawdown is not None and max_drawdown < GREENLIGHT_MAX_DRAWDOWN
        and n_trades > GREENLIGHT_MIN_TRADES
        and walk_forward_passed is True
    )
    if passes_greenlight:
        reasons.append(
            f"win_rate={win_rate:.2f}>{GREENLIGHT_MIN_WIN_RATE}, sharpe={sharpe:.2f}>{GREENLIGHT_MIN_SHARPE}, "
            f"calibración={calibration_score:.2f}>{GREENLIGHT_MIN_CALIBRATION}, drawdown={max_drawdown:.2f}<{GREENLIGHT_MAX_DRAWDOWN}, "
            f"n={n_trades}>{GREENLIGHT_MIN_TRADES}, walk-forward pasó, sin violaciones anti-look-ahead"
        )
        return {
            "option": "A",
            "label": "GREENLIGHT — READY FOR PHASE 2",
            "recommendation": "Invierte con capital de prueba pequeño (ver presupuesto recomendado en PARTE 7).",
            "reasons": reasons,
        }

    triggers_redlight = (
        (sharpe is not None and sharpe < REDLIGHT_MAX_SHARPE)
        or (win_rate is not None and win_rate < REDLIGHT_MIN_WIN_RATE)
        or (calibration_score is not None and calibration_score < REDLIGHT_MIN_CALIBRATION)
    )
    if triggers_redlight:
        if sharpe is not None and sharpe < REDLIGHT_MAX_SHARPE:
            reasons.append(f"sharpe={sharpe:.2f} < {REDLIGHT_MAX_SHARPE}")
        if win_rate is not None and win_rate < REDLIGHT_MIN_WIN_RATE:
            reasons.append(f"win_rate={win_rate:.2f} < {REDLIGHT_MIN_WIN_RATE}")
        if calibration_score is not None and calibration_score < REDLIGHT_MIN_CALIBRATION:
            reasons.append(f"calibración={calibration_score:.2f} < {REDLIGHT_MIN_CALIBRATION}")
        return {
            "option": "C",
            "label": "REDLIGHT — NOT READY",
            "recommendation": "Revisita con más datos o pivotar estrategia. No apto para capital real hoy.",
            "reasons": reasons,
        }

    # Ni A ni C -> B, documentando qué le faltó a A específicamente.
    # Los None se formatean como "None" literal (no hay nada que redondear);
    # los floats siempre con 2 decimales, nunca el repr crudo de Python.
    missing = []
    if win_rate is None or win_rate <= GREENLIGHT_MIN_WIN_RATE:
        missing.append(f"win_rate={'None' if win_rate is None else f'{win_rate:.2f}'}")
    if sharpe is None or sharpe <= GREENLIGHT_MIN_SHARPE:
        missing.append(f"sharpe={'None' if sharpe is None else f'{sharpe:.2f}'}")
    if calibration_score is None or calibration_score <= GREENLIGHT_MIN_CALIBRATION:
        missing.append(f"calibración={'None' if calibration_score is None else f'{calibration_score:.2f}'}")
    if max_drawdown is None or max_drawdown >= GREENLIGHT_MAX_DRAWDOWN:
        missing.append(f"drawdown={'None' if max_drawdown is None else f'{max_drawdown:.2f}'}")
    if n_trades <= GREENLIGHT_MIN_TRADES:
        missing.append(f"n={n_trades} (mínimo {GREENLIGHT_MIN_TRADES})")
    if walk_forward_passed is not True:
        missing.append(f"walk_forward_passed={walk_forward_passed}")
    reasons.append(f"No cumple todos los criterios de GREENLIGHT, pero tampoco dispara REDLIGHT: {', '.join(missing)}")

    return {
        "option": "B",
        "label": "YELLOWLIGHT — CONDITIONALLY VIABLE",
        "recommendation": "Invierte capital mínimo en la fuente de datos más barata. Paper trade 3 meses más antes de live.",
        "reasons": reasons,
    }
