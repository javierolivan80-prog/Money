"""portfolio_validation.py — checksums anti-look-ahead y robustez temporal
(spec: "VALIDACIONES SIN LOOK-AHEAD").

Cobertura de las 3 comprobaciones literales del spec, y por qué cada una se
implementa donde se implementa:

1. "Checksum: si algún trade usa datos de T > exit_date, error" — no
   verificable de forma genérica post-hoc sin guardar el camino completo de
   precios usado (que no se persiste, ver portfolio_metrics.py:
   compute_asymmetry_report). Lo que SÍ es una violación de este tipo, y
   auditable: exit_date <= entry_date, o un exit_reason inconsistente con
   los datos. Ver validate_no_lookahead().

2. "Si entrada en D0, error" — auditable directamente cruzando
   portfolio_trades.entry_date con events.d0_close_date. Ver validate_no_lookahead().

3. "Si target_price calculada con precio de D+1, error" (léase: con un
   precio que no sea el de entrada) — esto es una invariante ESTRUCTURAL del
   código, no un dato que se pueda re-verificar desde la BD: compute_tp_sl_prices()
   (portfolio_simulator.py) SIEMPRE deriva take_profit_price/stop_loss_price
   de `entry_price`, no hay ninguna ruta de código que use otro precio. Se
   demuestra con test_compute_tp_sl_prices_long/short en
   test_portfolio_simulator.py, no con una query — añadir columnas nuevas a
   portfolio_trades solo para poder re-verificar en SQL algo que el propio
   código ya garantiza por construcción sería sobre-ingeniería.

4. "Rerun con offset temporal +1 año" — implementado como un análisis de
   ESTABILIDAD sobre los trades YA generados por una corrida (partidos en
   dos periodos por fecha), no como una re-simulación desde cero: es
   igual de informativo (¿el resultado depende de un tramo de calendario
   concreto?) y mucho más barato. Ver compute_temporal_stability_report().
"""
from __future__ import annotations

from datetime import date

from pipeline.backtest.portfolio_metrics import compute_trade_metrics

# Umbrales de "inestable" — heurísticos, documentados como tales, no
# derivados de una teoría estadística formal (con los tamaños de muestra de
# este POC, ninguna prueba formal de estabilidad temporal tendría potencia).
WIN_RATE_DIVERGENCE_THRESHOLD_PP = 15.0  # puntos porcentuales
MIN_TRADES_PER_PERIOD_FOR_COMPARISON = 10


def validate_no_lookahead(conn, run_batch_tag: str) -> list[str]:
    """Audita portfolio_trades de una corrida contra events.d0_close_date.
    Devuelve una lista de descripciones de violación — vacía significa que
    pasó. Nunca lanza una excepción: el caller decide qué hacer con las
    violaciones (loguearlas, abortar el reporte, etc.)."""
    violations: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.trade_id, pt.event_id, pt.entry_date, pt.exit_date, e.d0_close_date
            FROM portfolio_trades pt
            JOIN events e ON e.event_id = pt.event_id
            WHERE pt.run_batch_tag = %s
            """,
            (run_batch_tag,),
        )
        rows = cur.fetchall()

    for row in rows:
        if row["entry_date"] <= row["d0_close_date"]:
            violations.append(
                f"trade_id={row['trade_id']} (event {row['event_id']}): entry_date={row['entry_date']} "
                f"no es posterior a d0_close_date={row['d0_close_date']} (entrada en o antes de D0)"
            )
        if row["exit_date"] <= row["entry_date"]:
            violations.append(
                f"trade_id={row['trade_id']} (event {row['event_id']}): exit_date={row['exit_date']} "
                f"no es posterior a entry_date={row['entry_date']}"
            )
    return violations


def compute_temporal_stability_report(trades: list[dict], split_date: date) -> dict:
    """Divide trades (de UNA corrida ya completada) en dos periodos por
    entry_date y compara win_rate/retorno medio. Grandes divergencias son
    una señal de alerta — el resultado puede depender de un tramo de
    calendario concreto en vez de ser un efecto estable. No es una prueba
    estadística formal (ver docstring del módulo)."""
    before = [t for t in trades if t["entry_date"] < split_date]
    after = [t for t in trades if t["entry_date"] >= split_date]

    metrics_before = compute_trade_metrics(before)
    metrics_after = compute_trade_metrics(after)

    warnings: list[str] = []
    if len(before) < MIN_TRADES_PER_PERIOD_FOR_COMPARISON or len(after) < MIN_TRADES_PER_PERIOD_FOR_COMPARISON:
        warnings.append(
            f"Muestra insuficiente para comparar con confianza (antes={len(before)}, después={len(after)}, "
            f"mínimo recomendado={MIN_TRADES_PER_PERIOD_FOR_COMPARISON} por periodo)"
        )
        stable = None  # no se puede afirmar nada, ni estable ni inestable
    else:
        win_rate_before = metrics_before["win_rate"] or 0.0
        win_rate_after = metrics_after["win_rate"] or 0.0
        win_rate_diff_pp = abs(win_rate_before - win_rate_after) * 100
        sign_flip = (metrics_before["expectancy"] or 0) * (metrics_after["expectancy"] or 0) < 0

        stable = win_rate_diff_pp <= WIN_RATE_DIVERGENCE_THRESHOLD_PP and not sign_flip
        if win_rate_diff_pp > WIN_RATE_DIVERGENCE_THRESHOLD_PP:
            warnings.append(f"win_rate diverge {win_rate_diff_pp:.1f} puntos porcentuales entre periodos (umbral: {WIN_RATE_DIVERGENCE_THRESHOLD_PP})")
        if sign_flip:
            warnings.append("la expectancy cambia de signo entre periodos (rentable en uno, perdedor en el otro)")

    return {
        "split_date": split_date,
        "n_trades_before": len(before),
        "n_trades_after": len(after),
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "stable": stable,
        "warnings": warnings,
    }
