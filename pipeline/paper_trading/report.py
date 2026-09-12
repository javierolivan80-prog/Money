"""report.py — Fase 4: ensambla el reporte semanal completo de paper
trading (DAILY RESULTS, comparación predicción-vs-real, calibración, alerts,
comparación con el backtest histórico) y lo persiste, mismo patrón que
backtest/portfolio_report.py (JSONB en una tabla, para que el dashboard no
recalcule nada)."""
from __future__ import annotations

import json
import logging
from datetime import date

from pipeline.backtest.portfolio_metrics import compute_calibration_diagnostics, compute_trade_metrics
from pipeline.backtest.portfolio_simulator import gain_pct
from pipeline.paper_trading.analysis import compute_alerts, compute_prediction_accuracy
from pipeline.paper_trading.simulator import VERSIONS, load_ticker_prices, select_simulation_week, simulate_paper_trading_week

logger = logging.getLogger(__name__)

# Umbral de la comparación "¿el paper trading confirma el backtest?" — mucho
# más laxo que el de compute_temporal_stability_report (15pp, sobre cientos
# de trades) porque una semana de paper trading trae unos pocos trades: una
# divergencia grande ahí es la norma estadística, no una señal de alarma.
# Documentado como convención, no derivado de una teoría formal — igual que
# el resto de umbrales heurísticos del proyecto.
SANITY_CHECK_WIN_RATE_DIVERGENCE_PP = 30.0


def _fetch_paper_trades(conn, version: str, run_batch_tag: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.*, e.ticker, e.event_class
            FROM paper_trades pt
            JOIN events e ON e.event_id = pt.event_id
            WHERE pt.version = %s AND pt.run_batch_tag = %s
            ORDER BY pt.entry_date
            """,
            (version, run_batch_tag),
        )
        return cur.fetchall()


def _serialize_open_position(conn, t: dict, ticker_cache: dict[str, dict[date, dict]]) -> dict:
    ticker = t["ticker"]
    if ticker not in ticker_cache:
        ticker_cache[ticker] = load_ticker_prices(conn, ticker)
    prices = ticker_cache[ticker]
    latest_date = max(prices.keys()) if prices else None
    current_price = float(prices[latest_date]["close_raw"]) if latest_date and prices[latest_date]["close_raw"] is not None else None
    unrealized_pnl_pct = (
        gain_pct(t["direction"], float(t["entry_price"]), current_price) if current_price is not None else None
    )
    return {
        "event_id": t["event_id"],
        "ticker": ticker,
        "event_class": t["event_class"],
        "direction": t["direction"],
        "entry_date": t["entry_date"].isoformat(),
        "entry_price": float(t["entry_price"]),
        "current_price": current_price,
        "current_price_date": latest_date.isoformat() if latest_date else None,
        "unrealized_pnl_pct": unrealized_pnl_pct,
    }


def _serialize_closed_trade(t: dict) -> dict:
    return {
        "event_id": t["event_id"],
        "ticker": t["ticker"],
        "event_class": t["event_class"],
        "direction": t["direction"],
        "entry_date": t["entry_date"].isoformat(),
        "entry_price": float(t["entry_price"]),
        "exit_date": t["exit_date"].isoformat(),
        "exit_price": float(t["exit_price"]),
        "exit_reason": t["exit_reason"],
        "status": t["status"],
        "pnl_pct": float(t["pnl_pct"]),
    }


def compare_with_historical_backtest(conn, version: str, week_trade_metrics: dict) -> dict:
    """Sanity check del spec ("¿papel matches backtest histórico?") — compara
    el win_rate de esta semana contra el del backtest histórico más
    reciente (portfolio_reports, Fase 3). Con la muestra de una sola semana,
    esto es una señal blanda, no una prueba — ver SANITY_CHECK_WIN_RATE_DIVERGENCE_PP."""
    with conn.cursor() as cur:
        cur.execute("SELECT report_json FROM portfolio_reports ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    if not row:
        return {"available": False, "note": "sin backtest histórico corrido todavía (backtest/portfolio_report.py)"}

    hist_version = row["report_json"].get("versions", {}).get(version)
    if not hist_version:
        return {"available": False, "note": f"el backtest histórico más reciente no tiene datos de {version}"}

    hist_win_rate = hist_version["trade_metrics"]["win_rate"]
    week_win_rate = week_trade_metrics["win_rate"]
    if hist_win_rate is None or week_win_rate is None:
        return {"available": True, "comparable": False, "note": "muestra insuficiente en alguno de los dos lados para comparar win_rate"}

    diff_pp = abs(hist_win_rate - week_win_rate) * 100
    return {
        "available": True,
        "comparable": True,
        "historical_win_rate": hist_win_rate,
        "week_win_rate": week_win_rate,
        "diff_pp": diff_pp,
        "matches_historical": diff_pp <= SANITY_CHECK_WIN_RATE_DIVERGENCE_PP,
    }


def build_version_report(conn, version: str, week_start: date, week_end: date, run_batch_tag: str) -> dict:
    all_trades = _fetch_paper_trades(conn, version, run_batch_tag)
    open_trades = [t for t in all_trades if t["status"] == "OPEN"]
    closed_trades = [t for t in all_trades if t["status"] != "OPEN"]

    trade_metrics = compute_trade_metrics(closed_trades)
    predictions = compute_prediction_accuracy(conn, version, week_start, week_end)
    predictions_with_outcome = [p for p in predictions if p["was_correct"] is not None]
    calibration = compute_calibration_diagnostics(predictions_with_outcome, "confidence_given", "was_correct")
    alerts = compute_alerts(closed_trades, predictions, version)
    comparison = compare_with_historical_backtest(conn, version, trade_metrics)

    ticker_cache: dict[str, dict[date, dict]] = {}
    open_positions = [_serialize_open_position(conn, t, ticker_cache) for t in open_trades]
    closed_serialized = sorted((_serialize_closed_trade(t) for t in closed_trades), key=lambda t: t["exit_date"], reverse=True)

    by_error = sorted(predictions_with_outcome, key=lambda p: p["error"])
    top_3_best_predicted = by_error[:3]
    top_3_worst_predicted = list(reversed(by_error[-3:])) if by_error else []

    return {
        "version": version,
        "n_open_positions": len(open_positions),
        "n_closed_trades": len(closed_trades),
        "open_positions": open_positions,
        "last_10_closed_trades": closed_serialized[:10],
        "cumulative_pnl_pct_week": sum(t["pnl_pct"] for t in closed_serialized) if closed_serialized else 0.0,
        "trade_metrics": trade_metrics,
        "predictions": predictions,
        "calibration": calibration,
        "alerts": alerts,
        "comparison_with_historical_backtest": comparison,
        "top_3_best_predicted": top_3_best_predicted,
        "top_3_worst_predicted": top_3_worst_predicted,
    }


def run_paper_trading_report(conn, run_batch_tag: str | None = None, as_of: date | None = None) -> dict:
    """Punto de entrada único — corre las 3 versiones para la última semana
    completa disponible (o la que contenga `as_of`, para tests) y ensambla
    el reporte semanal completo.

    run_batch_tag por defecto se deriva de la SEMANA simulada
    (paper-week-{week_start}), NO de la fecha de hoy — a propósito: el
    pipeline nocturno corre esto todas las noches (nightly_pipeline.yml), y
    las posiciones OPEN de una semana deben resolverse (ON CONFLICT DO
    UPDATE sobre el MISMO run_batch_tag) a medida que llegan más días de
    precio, no acumular una fila nueva bajo un tag distinto cada noche —
    eso dejaría huérfanas para siempre las posiciones OPEN de noches
    anteriores. Un tag explícito sigue disponible para tests que necesiten
    aislar corridas del mismo período."""
    week_start, week_end = select_simulation_week(as_of)
    if run_batch_tag is None:
        run_batch_tag = f"paper-week-{week_start.isoformat()}"

    versions_report = {}
    for version in VERSIONS:
        logger.info("Paper trading %s — semana %s a %s (tag=%s)", version, week_start, week_end, run_batch_tag)
        simulate_paper_trading_week(conn, version, week_start, week_end, run_batch_tag)
        versions_report[version] = build_version_report(conn, version, week_start, week_end, run_batch_tag)

    report = {
        "run_batch_tag": run_batch_tag,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "versions": versions_report,
    }
    _store_report(conn, run_batch_tag, week_start, week_end, report)
    return report


def _store_report(conn, run_batch_tag: str, week_start: date, week_end: date, report: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_trading_reports (run_batch_tag, week_start, week_end, report_json)
            VALUES (%(tag)s, %(week_start)s, %(week_end)s, %(report)s)
            ON CONFLICT (run_batch_tag) DO UPDATE SET
                week_start = EXCLUDED.week_start, week_end = EXCLUDED.week_end,
                report_json = EXCLUDED.report_json, created_at = now()
            """,
            {"tag": run_batch_tag, "week_start": week_start, "week_end": week_end, "report": json.dumps(report, default=str)},
        )
    conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    conn = get_connection()
    report = run_paper_trading_report(conn)  # run_batch_tag derivado de la semana, ver docstring

    print(f"=== Paper trading — semana {report['week_start']} a {report['week_end']} (tag={report['run_batch_tag']}) ===")
    for version, v_report in report["versions"].items():
        print(f"\n--- {version} ---")
        print(f"Abiertas: {v_report['n_open_positions']} · Cerradas: {v_report['n_closed_trades']}")
        print(json.dumps(v_report["trade_metrics"], indent=2, default=str))
        for alert in v_report["alerts"]:
            print(f"[{alert['type']}] {alert['message']}")
