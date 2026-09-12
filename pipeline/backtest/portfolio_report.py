"""portfolio_report.py — ensambla el reporte completo de una corrida
(spec: "OUTPUTS DAY 4-5") y la recomendación final.

El dashboard de Next.js (app/, ver RUNBOOK.md §3.9 y §4) lee este reporte
completo desde `portfolio_reports.report_json` y lo renderiza — todas las
fórmulas (Sharpe/Sortino/Calmar, calibración, buckets de confidence, etc.)
viven aquí y en portfolio_metrics.py, nunca reimplementadas en TypeScript.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from pipeline.backtest.portfolio_metrics import (
    CALIBRATION_TARGET,
    compute_asymmetry_report,
    compute_bias_report,
    compute_calibration,
    compute_calibration_diagnostics,
    compute_equity_metrics,
    compute_metrics_by_event_type,
    compute_prediction_regression,
    compute_trade_metrics,
    top_n_trades,
)
from pipeline.backtest.portfolio_simulator import VERSIONS, simulate_portfolio
from pipeline.backtest.portfolio_validation import compute_temporal_stability_report, validate_no_lookahead

logger = logging.getLogger(__name__)

# Umbrales de la recomendación final — convencionales/heurísticos,
# documentados como tales (no derivados de una teoría formal, igual que los
# de compute_temporal_stability_report). Ver generate_recommendation().
MIN_TRADES_FOR_ANY_CONCLUSION = 20
SHARPE_TARGET = 1.0
MAX_DRAWDOWN_CEILING = 0.25


def _fetch_risk_free_daily(conn) -> dict[date, float]:
    with conn.cursor() as cur:
        cur.execute("SELECT trade_date, rf FROM fama_french_factors")
        return {r["trade_date"]: float(r["rf"]) for r in cur.fetchall()}


def _fetch_trades(conn, version: str, run_batch_tag: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.*, e.event_class, e.ticker
            FROM portfolio_trades pt
            JOIN events e ON e.event_id = pt.event_id
            WHERE pt.version = %s AND pt.run_batch_tag = %s
            ORDER BY pt.entry_date
            """,
            (version, run_batch_tag),
        )
        return cur.fetchall()


def _fetch_equity_curve(conn, version: str, run_batch_tag: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, balance, n_open_positions FROM portfolio_equity_curve "
            "WHERE version = %s AND run_batch_tag = %s ORDER BY trade_date",
            (version, run_batch_tag),
        )
        return cur.fetchall()


def build_version_report(conn, version: str, run_batch_tag: str, starting_capital: float = 100_000.0) -> dict:
    """Todo lo que el spec pide "por versión": métricas, submétricas por
    clase de evento, calibración, regresión, top 10, y las violaciones
    anti-look-ahead de esta corrida (siempre se calculan, nunca se asume que
    pasan)."""
    trades = _fetch_trades(conn, version, run_batch_tag)
    equity_curve = _fetch_equity_curve(conn, version, run_batch_tag)
    risk_free = _fetch_risk_free_daily(conn)

    trade_metrics = compute_trade_metrics(trades)
    equity_metrics = compute_equity_metrics(equity_curve, starting_capital, risk_free_daily=risk_free)
    by_event_type = compute_metrics_by_event_type(trades)
    calibration = compute_calibration(trades)
    regression = compute_prediction_regression(trades)
    asymmetry = compute_asymmetry_report(trades)
    violations = validate_no_lookahead(conn, run_batch_tag)

    stability = None
    if trades:
        entry_dates = sorted(t["entry_date"] for t in trades)
        span_days = (entry_dates[-1] - entry_dates[0]).days
        if span_days > 60:  # con menos de ~2 meses de rango, partir en dos no aporta nada
            split = entry_dates[0] + timedelta(days=span_days // 2)
            stability = compute_temporal_stability_report(trades, split)

    # Calibración confidence-vs-resultado (dashboard Fase 5, TAB 2 "win rate
    # by confidence bucket" Y TAB 3 "calibration curve" — mismo cálculo,
    # compute_calibration_diagnostics ya incluye los buckets). Un trade
    # "gana" si pnl_pct>0, misma convención conservadora que
    # compute_trade_metrics (0 exacto cuenta como perdedor). Distinto del
    # campo `calibration` de arriba (fórmula 1-|pred-real|/|pred| sobre EV
    # vs retorno, no sobre confidence vs acierto) — dos preguntas distintas,
    # ver RUNBOOK.md §3.10 sobre por qué no se unifican.
    confidence_calibration = compute_calibration_diagnostics(
        [{"confidence": float(t["confidence"]), "won": float(t["pnl_pct"]) > 0} for t in trades],
        "confidence",
        "won",
    )

    return {
        "version": version,
        "run_batch_tag": run_batch_tag,
        "trade_metrics": trade_metrics,
        "equity_metrics": equity_metrics,
        "equity_curve": [{"trade_date": r["trade_date"].isoformat(), "balance": float(r["balance"])} for r in equity_curve],
        "metrics_by_event_type": by_event_type,
        "confidence_calibration": confidence_calibration,
        "calibration": calibration,
        "prediction_regression": regression,
        "asymmetry": asymmetry,
        # all_trades: alimenta el feed "ALL SIGNALS" (TAB 1) y el histograma
        # de retornos (TAB 2) — top_10_winners/losers no alcanza para esos
        # dos usos, que necesitan la distribución completa, no solo los
        # extremos.
        "all_trades": [_serialize_trade(t) for t in trades],
        "top_10_winners": [_serialize_trade(t) for t in top_n_trades(trades, 10, winners=True)],
        "top_10_losers": [_serialize_trade(t) for t in top_n_trades(trades, 10, winners=False)],
        "no_lookahead_violations": violations,
        "temporal_stability": stability,
    }


def _serialize_trade(t: dict) -> dict:
    return {
        "event_id": t["event_id"],
        "ticker": t.get("ticker"),
        "event_class": t.get("event_class"),
        "direction": t["direction"],
        "entry_date": t["entry_date"].isoformat(),
        "exit_date": t["exit_date"].isoformat(),
        "exit_reason": t["exit_reason"],
        "pnl_pct": float(t["pnl_pct"]),
        "confidence": float(t["confidence"]),
        "ev": float(t["ev"]),
    }


def run_full_backtest(conn, run_batch_tag: str, starting_capital: float = 100_000.0) -> dict:
    """Corre las 3 versiones y ensambla el reporte completo — el punto de
    entrada único para el "output día 4-5" del spec."""
    version_reports = {}
    for version in VERSIONS:
        logger.info("Simulando cartera %s (tag=%s)", version, run_batch_tag)
        simulate_portfolio(conn, version, run_batch_tag, starting_capital)
        version_reports[version] = build_version_report(conn, version, run_batch_tag, starting_capital)

    bias_report = compute_bias_report(conn)
    recommendation = generate_recommendation(version_reports)

    report = {
        "run_batch_tag": run_batch_tag,
        "starting_capital": starting_capital,
        "versions": version_reports,
        "bias_report": bias_report,
        "recommendation": recommendation,
    }
    _store_report(conn, run_batch_tag, report)
    return report


def _store_report(conn, run_batch_tag: str, report: dict) -> None:
    """Persiste el reporte completo en portfolio_reports — así el dashboard
    de Next.js (app/, solo lectura) lo renderiza sin reimplementar ninguna
    fórmula de portfolio_metrics.py en TypeScript/SQL. default=str por
    temporal_stability.split_date (un date, no serializable directo por
    json.dumps) — el resto del árbol ya son tipos JSON nativos."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO portfolio_reports (run_batch_tag, report_json)
            VALUES (%(tag)s, %(report)s)
            ON CONFLICT (run_batch_tag) DO UPDATE SET
                report_json = EXCLUDED.report_json, created_at = now()
            """,
            {"tag": run_batch_tag, "report": json.dumps(report, default=str)},
        )
    conn.commit()


def generate_recommendation(version_reports: dict[str, dict]) -> dict:
    """"Decision: ¿vale la pena meter dinero real?" — criterios explícitos,
    convencionales pero arbitrarios (documentados como tales, ver constantes
    del módulo): Sharpe > 1.0, max_drawdown < 25%, calibración > 0.6 (el
    umbral del propio spec), al menos 20 trades, y CERO violaciones
    anti-look-ahead (no negociable — un resultado con violaciones no es un
    resultado, es un artefacto de un bug). Ninguna versión que las cumpla
    todas -> "no todavía", no un "no" definitivo: puede significar que falta
    n (ver AUDIT_LEAN.md sobre MDE), no que el concepto esté muerto."""
    findings = []
    any_version_passes = False

    for version, report in version_reports.items():
        if report["no_lookahead_violations"]:
            findings.append(f"{version}: {len(report['no_lookahead_violations'])} violación(es) anti-look-ahead — resultado no fiable, corregir antes de interpretar nada más")
            continue

        n_trades = report["trade_metrics"]["total_trades"]
        if n_trades < MIN_TRADES_FOR_ANY_CONCLUSION:
            findings.append(f"{version}: solo {n_trades} trades (mínimo {MIN_TRADES_FOR_ANY_CONCLUSION}) — muestra insuficiente para concluir nada")
            continue

        eq = report["equity_metrics"]
        cal = report["calibration"]
        sharpe = eq["sharpe_ratio"]
        drawdown = eq["max_drawdown"]
        calibration_score = cal["calibration_score"]

        passes_sharpe = sharpe is not None and sharpe > SHARPE_TARGET
        passes_drawdown = drawdown is not None and drawdown < MAX_DRAWDOWN_CEILING
        passes_calibration = calibration_score is not None and calibration_score > CALIBRATION_TARGET

        if passes_sharpe and passes_drawdown and passes_calibration:
            any_version_passes = True
            findings.append(
                f"{version}: SUPERA los 3 criterios — Sharpe={sharpe:.2f} (>{SHARPE_TARGET}), "
                f"max_drawdown={drawdown*100:.1f}% (<{MAX_DRAWDOWN_CEILING*100:.0f}%), "
                f"calibración={calibration_score:.2f} (>{CALIBRATION_TARGET})"
            )
        else:
            missing = []
            if not passes_sharpe:
                missing.append(f"Sharpe={sharpe:.2f}" if sharpe is not None else "Sharpe=None")
            if not passes_drawdown:
                missing.append(f"max_drawdown={drawdown*100:.1f}%" if drawdown is not None else "max_drawdown=None")
            if not passes_calibration:
                missing.append(f"calibración={calibration_score:.2f}" if calibration_score is not None else "calibración=None")
            findings.append(f"{version}: no supera todos los criterios ({', '.join(missing)})")

    verdict = "SÍ, con capital de prueba pequeño y solo en la(s) versión(es) que superan los 3 criterios" if any_version_passes else "NO todavía"
    return {"verdict": verdict, "findings": findings}


if __name__ == "__main__":
    import json
    import subprocess

    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    # run_batch_tag: fecha + git sha corto, igual que backtest_runs (Fase 1,
    # T9 de ARCHITECTURE_LEAN.md — reproducibilidad).
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        git_sha = "unknown"
    tag = f"{date.today().isoformat()}-{git_sha}"

    conn = get_connection()
    report = run_full_backtest(conn, run_batch_tag=tag)

    print(f"=== Backtest de cartera — {tag} ===")
    print(json.dumps(report["recommendation"], indent=2, ensure_ascii=False))
    for version, v_report in report["versions"].items():
        print(f"\n--- {version} ---")
        print(json.dumps(v_report["trade_metrics"], indent=2))
        print(json.dumps(v_report["equity_metrics"], indent=2))
        if v_report["no_lookahead_violations"]:
            print(f"⚠ {len(v_report['no_lookahead_violations'])} VIOLACIONES ANTI-LOOK-AHEAD")
