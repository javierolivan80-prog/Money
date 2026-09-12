"""analysis.py — Fase 4: "COMPARACIÓN PREDICTION vs ACTUAL" y ALERTS.

CONVENCIÓN DE "predicted_magnitude" (documentada porque el spec da un
ejemplo en una escala que no corresponde a ningún dato de este proyecto):
el spec ilustra con "predice +40%, sube +38%" — una magnitud grande que se
parece más a `impact_estimation.expected_magnitude_pct` (Etapa 6,
historical_analogues.py: un pronóstico de movimiento de precio bruto basado
en análogos históricos) que a `ev_conservative/aggressive/balanced` (Etapa 7:
un valor esperado ajustado por probabilidad y filtros, típicamente 0.2%-2%,
NO un pronóstico de movimiento). Pero expected_magnitude_pct solo se
persiste como texto formateado dentro del JSONB `impact_estimation`
("±X.XX% según histórico de N eventos análogos") — parsearlo de vuelta a
número con una regex sería frágil y invisible si el formato del string
cambia. Se usa `ev_{version}*100` como predicted_magnitude, la MISMA
definición que ya usa portfolio_metrics.compute_calibration para el
backtest histórico (Fase 3) — para que "calibración" signifique lo mismo en
todo el proyecto — y se recalibran los umbrales de los alerts a esta escala
real (0.2%-2%), no a los "+40%" ilustrativos del spec.

was_correct / actual_move_5d están en la MISMA convención "ajustada a
dirección" que portfolio_trades.actual_move_pct (positivo = la operación
habría ganado, sin importar LONG/SHORT) — no el retorno crudo del
subyacente. Mismo razonamiento: consistencia con el resto del proyecto por
encima de una lectura literal del ejemplo del spec.
"""
from __future__ import annotations

from datetime import date

from pipeline.backtest.portfolio_simulator import gain_pct
from pipeline.paper_trading.simulator import load_ticker_prices, fetch_all_events_for_week

ACTUAL_MOVE_HORIZON_TRADING_DAYS = 5

_EV_COLUMN_BY_VERSION = {
    "CONSERVATIVE": "ev_conservative",
    "AGGRESSIVE": "ev_aggressive",
    "BALANCED": "ev_balanced",
}

# Umbrales de los alerts — heurísticos, documentados como tales (mismo
# principio que el resto del proyecto: convención razonable, no derivada de
# una teoría formal). Recalibrados a la escala real de ev_* (0.2%-2%), ver
# docstring del módulo.
CONSECUTIVE_LOSSES_ALERT_THRESHOLD = 2
GOOD_PREDICTION_MIN_MAGNITUDE_PCT = 1.0  # "predicción grande" en la escala de ev*100
GOOD_PREDICTION_MAX_ERROR_PCT = 0.3  # "acertó de cerca"


def compute_prediction_accuracy(conn, version: str, week_start: date, week_end: date) -> list[dict]:
    """Una fila por evento de la semana (CON o SIN trade_decision — el spec
    pide evaluar la señal cruda para todos), comparando lo que predijo el
    modelo con lo que de verdad pasó en los 5 días de negociación
    siguientes a la entrada D+1. actual_move_5d es None si todavía no hay
    suficientes días de precio para saberlo (no se inventa un valor)."""
    ev_col = _EV_COLUMN_BY_VERSION[version]
    trade_decision_col = f"trade_decision_{version.lower()}"
    events = fetch_all_events_for_week(conn, week_start, week_end)

    ticker_cache: dict[str, dict[date, dict]] = {}
    results = []
    for ev in events:
        ticker = ev["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = load_ticker_prices(conn, ticker)
        prices = ticker_cache[ticker]

        future = sorted(d for d in prices if d > ev["d0_close_date"])
        if not future:
            continue
        entry_date = future[0]
        entry_bar = prices[entry_date]
        if entry_bar["open_raw"] is None:
            continue
        entry_price = float(entry_bar["open_raw"])

        predicted_direction = "LONG" if float(ev["prediction"]) > 0 else "SHORT"
        predicted_magnitude = float(ev[ev_col]) * 100

        future_after_entry = sorted(d for d in prices if d > entry_date)
        actual_move_5d = None
        if len(future_after_entry) >= ACTUAL_MOVE_HORIZON_TRADING_DAYS:
            exit_date_5d = future_after_entry[ACTUAL_MOVE_HORIZON_TRADING_DAYS - 1]
            close_5d = prices[exit_date_5d]["close_raw"]
            if close_5d is not None:
                actual_move_5d = gain_pct(predicted_direction, entry_price, float(close_5d))

        was_correct = None if actual_move_5d is None else actual_move_5d > 0
        error = None if actual_move_5d is None else abs(predicted_magnitude - actual_move_5d)
        confidence = float(ev["confidence"])

        results.append(
            {
                "event_id": ev["event_id"],
                "ticker": ticker,
                "event_class": ev["event_class"],
                "predicted_direction": predicted_direction,
                "predicted_magnitude": predicted_magnitude,
                "actual_move_5d": actual_move_5d,
                "error": error,
                "was_correct": was_correct,
                "confidence_given": confidence,
                "calibration_check": f"si confidence {confidence:.0f}%, esperamos ~{confidence:.0f}% de aciertos",
                "was_traded": ev[trade_decision_col] != "NO_TRADE",
            }
        )
    return results


def compute_alerts(closed_trades: list[dict], predictions: list[dict], version: str) -> list[dict]:
    """Dos tipos de alert que pide el spec, literalmente:
    1. 2+ pérdidas consecutivas en la versión -> "revisame, posible overfitting".
    2. Una predicción grande que acertó de cerca -> "good prediction".
    Reutiliza compute_trade_metrics para las rachas (no reimplementa la
    lógica de streak, ya probada en portfolio_metrics)."""
    from pipeline.backtest.portfolio_metrics import compute_trade_metrics

    alerts = []
    metrics = compute_trade_metrics(closed_trades)
    if metrics["consecutive_losses"] >= CONSECUTIVE_LOSSES_ALERT_THRESHOLD:
        alerts.append(
            {
                "type": "WARNING",
                "version": version,
                "message": f"revisame, posible overfitting — {metrics['consecutive_losses']} pérdidas consecutivas en {version}",
            }
        )

    for p in predictions:
        if p["error"] is None:
            continue
        if abs(p["predicted_magnitude"]) >= GOOD_PREDICTION_MIN_MAGNITUDE_PCT and p["error"] <= GOOD_PREDICTION_MAX_ERROR_PCT:
            alerts.append(
                {
                    "type": "CONGRATULATE",
                    "version": version,
                    "ticker": p["ticker"],
                    "event_id": p["event_id"],
                    "message": f"good prediction — {p['ticker']}: predicho {p['predicted_magnitude']:+.2f}%, real {p['actual_move_5d']:+.2f}%",
                }
            )
    return alerts
