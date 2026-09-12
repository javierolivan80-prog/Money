"""event_study.py — Fase 6 PARTE 1 ("EVENT STUDY — validez del concepto").

Distinto del backtest (portfolio_report.py): el backtest mide si UNA
ESTRATEGIA de trading (con TP/SL/sizing/comisiones) ganó dinero; el event
study mide si el EVENTO EN SÍ mueve el precio de forma no aleatoria —
la pregunta de fondo que AUDIT_LEAN.md §2.2.3 ya identificó como "la que
responde ¿existe un edge?", sobre car_results (CAR de cada evento,
poblado por backtest/populate_car_results.py), no sobre portfolio_trades.

MDE = 2.8 · σ / √n (80% potencia, bilateral α=0.05) — la MISMA fórmula y
constante que AUDIT_LEAN.md §2.2.3 ya usó para argumentar la viabilidad
del proyecto. Se reutiliza aquí literalmente, no se deriva de nuevo, para
que el número que se cite en el reporte final sea el mismo concepto medido
dos veces (una vez proyectado sobre n esperado en el audit, otra vez sobre
el n real que terminó habiendo) — no dos fórmulas distintas con el mismo
nombre.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

MDE_POWER_CONSTANT = 2.8  # ver docstring del módulo — AUDIT_LEAN.md §2.2.3
SIGNIFICANCE_ALPHA = 0.05
MIN_N_FOR_ANY_STATISTIC = 3  # por debajo de esto, ni sigma tiene sentido


def compute_mde(sigma: float, n: int) -> float | None:
    """Efecto mínimo detectable — ver docstring del módulo. None si n<=0."""
    if n <= 0 or sigma is None:
        return None
    return MDE_POWER_CONSTANT * sigma / np.sqrt(n)


def _fetch_car_by_class(conn, window_days: int) -> dict[str, list[float]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_class, cr.car
            FROM car_results cr
            JOIN events e ON e.event_id = cr.event_id
            WHERE cr.window_days = %s
            """,
            (window_days,),
        )
        rows = cur.fetchall()
    by_class: dict[str, list[float]] = {}
    for r in rows:
        by_class.setdefault(r["event_class"], []).append(float(r["car"]))
    return by_class


def compute_event_study_for_class(car_values: list[float]) -> dict:
    """Estadísticos de una sola clase de evento: media, mediana,
    percentiles, sigma, MDE, y t-test de una muestra contra H0: media=0
    (¿el retorno del evento es significativamente distinto de cero?)."""
    n = len(car_values)
    if n < MIN_N_FOR_ANY_STATISTIC:
        return {
            "n": n, "mean_return_pct": None, "median_return_pct": None,
            "p25_pct": None, "p75_pct": None, "sigma_pct": None, "mde_pct": None,
            "t_statistic": None, "p_value": None, "significant": None,
            "conclusion": f"n={n} — muestra insuficiente para cualquier estadístico (mínimo {MIN_N_FOR_ANY_STATISTIC})",
        }

    arr = np.array(car_values) * 100  # a puntos porcentuales
    mean = float(arr.mean())
    median = float(np.median(arr))
    sigma = float(arr.std(ddof=1))
    p25, p75 = (float(x) for x in np.percentile(arr, [25, 75]))
    mde = compute_mde(sigma, n)

    t_stat, p_value = stats.ttest_1samp(arr, popmean=0.0)
    t_stat, p_value = float(t_stat), float(p_value)
    significant = p_value < SIGNIFICANCE_ALPHA

    if significant:
        conclusion = f"Significativo (p={p_value:.4f} < {SIGNIFICANCE_ALPHA}) — el evento SÍ mueve el precio de forma no aleatoria"
    else:
        detectable_note = f"MDE={mde:.0f} bps con esta n" if mde is not None else ""
        conclusion = f"No significativo (p={p_value:.4f} >= {SIGNIFICANCE_ALPHA}) — {detectable_note}, podría ser ruido o un efecto real más pequeño que el MDE"

    return {
        "n": n,
        "mean_return_pct": mean,
        "median_return_pct": median,
        "p25_pct": p25,
        "p75_pct": p75,
        "sigma_pct": sigma,
        "mde_pct": mde,
        "t_statistic": t_stat,
        "p_value": p_value,
        "significant": significant,
        "conclusion": conclusion,
    }


def run_event_study(conn, window_days: int = 20) -> dict[str, dict]:
    """Punto de entrada — una fila de compute_event_study_for_class por
    event_class presente en car_results para la ventana dada. window_days=20
    por defecto (deriva histórica, no la reacción inmediata de 5 días) —
    mismo horizonte que AUDIT_LEAN.md §2.2.3 usa en su tabla de ejemplo."""
    by_class = _fetch_car_by_class(conn, window_days)
    return {event_class: compute_event_study_for_class(values) for event_class, values in by_class.items()}
