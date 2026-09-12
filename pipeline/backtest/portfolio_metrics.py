"""portfolio_metrics.py — métricas de la cartera simulada (portfolio_trades +
portfolio_equity_curve), sesgos, y calibración predicho-vs-real.

Todo aquí es PURO (recibe listas de dicts ya recuperadas de Postgres) salvo
compute_bias_report(), que sí consulta la BD directamente porque el sesgo de
supervivencia se mide sobre `universe`/`prices` en conjunto, no sobre los
trades de una corrida concreta.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import numpy as np

TRADING_DAYS_PER_YEAR = 252
INSUFFICIENT_SAMPLE_THRESHOLD = 20
CALIBRATION_TARGET = 0.6


# ============================================================================
# Métricas a nivel trade (no requieren la curva de equity)
# ============================================================================

def compute_trade_metrics(trades: list[dict]) -> dict:
    """trades: dicts con al menos 'pnl_pct' y 'exit_date' (para el orden
    cronológico de las rachas)."""
    if not trades:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0, "win_rate": None,
            "profit_factor": None, "avg_winner": None, "avg_loser": None, "expectancy": None,
            "largest_win": None, "largest_loss": None,
            "consecutive_wins": 0, "consecutive_losses": 0,
        }

    pnls = [float(t["pnl_pct"]) for t in trades]
    # Un trade con pnl exactamente 0 cuenta como perdedor (convención
    # conservadora: no ganó nada) — documentado, no un edge case oculto.
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    n = len(pnls)
    win_rate = len(winners) / n

    gross_profit = sum(winners)
    gross_loss = sum(losers)  # <= 0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss < 0 else None

    avg_winner = (sum(winners) / len(winners)) if winners else None
    avg_loser = (sum(losers) / len(losers)) if losers else None
    expectancy = win_rate * (avg_winner or 0.0) - (1 - win_rate) * abs(avg_loser or 0.0)

    chronological = sorted(trades, key=lambda t: t["exit_date"])
    chrono_pnls = [float(t["pnl_pct"]) for t in chronological]
    consecutive_wins = _max_streak(chrono_pnls, lambda p: p > 0)
    consecutive_losses = _max_streak(chrono_pnls, lambda p: p <= 0)

    return {
        "total_trades": n,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_winner": avg_winner,
        "avg_loser": avg_loser,
        "expectancy": expectancy,
        "largest_win": max(pnls),   # literal del spec: max/min sobre TODOS los pnl_pct
        "largest_loss": min(pnls),
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
    }


def _max_streak(values_chronological: list[float], predicate) -> int:
    best = current = 0
    for v in values_chronological:
        if predicate(v):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


# ============================================================================
# Métricas de la curva de equity
# ============================================================================

def compute_equity_metrics(
    equity_curve: list[dict],
    starting_capital: float,
    risk_free_daily: dict[date, float] | None = None,
) -> dict:
    """equity_curve: dicts con 'trade_date' y 'balance', en orden cronológico.

    annual_return: retorno SIMPLE anualizado (total_return / n_años), no
    compuesto — más fácil de interpretar para un POC y consistente con cómo
    el spec describe la fórmula ("total_return / n_years"), no con un CAGR.

    risk_free_daily: tasa libre de riesgo diaria por fecha, opcional. Si se
    pasa, se resta de los retornos diarios antes de Sharpe/Sortino (más
    correcto que asumir rf=0); se puede poblar desde
    fama_french_factors.rf, que el proyecto ya descarga (Fase 1)."""
    if not equity_curve:
        return _empty_equity_metrics()

    balances = [float(row["balance"]) for row in equity_curve]
    dates = [row["trade_date"] for row in equity_curve]
    final_balance = balances[-1]

    total_return = (final_balance - starting_capital) / starting_capital
    n_days = max((dates[-1] - dates[0]).days, 1)
    n_years = n_days / 365.25
    annual_return = total_return / n_years

    peak = balances[0]
    max_dd = 0.0
    for b in balances:
        peak = max(peak, b)
        if peak > 0:
            max_dd = min(max_dd, (b - peak) / peak)
    max_drawdown = abs(max_dd)

    daily_returns = []
    for i in range(1, len(balances)):
        if balances[i - 1] > 0:
            r = (balances[i] - balances[i - 1]) / balances[i - 1]
            rf = risk_free_daily.get(dates[i], 0.0) if risk_free_daily else 0.0
            daily_returns.append(r - rf)

    sharpe_ratio = sortino_ratio = None
    if len(daily_returns) >= 2:
        arr = np.array(daily_returns)
        std = arr.std(ddof=1)
        if std > 0:
            sharpe_ratio = float(arr.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR))
        downside = arr[arr < 0]
        if len(downside) >= 2 and downside.std(ddof=1) > 0:
            sortino_ratio = float(arr.mean() / downside.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        elif len(downside) == 1:
            sortino_ratio = float(arr.mean() / abs(downside[0]) * np.sqrt(TRADING_DAYS_PER_YEAR))

    calmar_ratio = (annual_return / max_drawdown) if max_drawdown > 0 else None
    recovery_factor = (total_return / max_drawdown) if max_drawdown > 0 else None

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "calmar_ratio": calmar_ratio,
        "recovery_factor": recovery_factor,
        "final_balance": final_balance,
    }


def _empty_equity_metrics() -> dict:
    return {k: None for k in ("total_return", "annual_return", "max_drawdown", "sharpe_ratio", "sortino_ratio", "calmar_ratio", "recovery_factor", "final_balance")}


# ============================================================================
# Submétricas por tipo de evento
# ============================================================================

def compute_metrics_by_event_type(trades_with_event_class: list[dict]) -> dict[str, dict]:
    """trades_with_event_class: cada dict trae 'event_class' además de
    'pnl_pct'. sharpe aquí es mean(pnl)/std(pnl) SIN anualizar — no es
    comparable directamente con el sharpe_ratio de compute_equity_metrics
    (ese sí está anualizado sobre retornos diarios de la curva de equity;
    este es un ratio riesgo/retorno POR TRADE, sobre una muestra con
    duraciones de holding distintas). Se documenta para no confundir ambos
    números al leer el reporte."""
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades_with_event_class:
        groups[t["event_class"]].append(float(t["pnl_pct"]))

    result = {}
    for event_class, pnls in groups.items():
        n = len(pnls)
        arr = np.array(pnls)
        win_rate = float((arr > 0).mean())
        avg_return = float(arr.mean())
        sharpe_per_trade = float(avg_return / arr.std(ddof=1)) if n >= 2 and arr.std(ddof=1) > 0 else None
        result[event_class] = {
            "event_type": event_class,
            "n_trades": n,
            "win_rate": win_rate,
            "avg_return": avg_return,
            "sharpe_per_trade": sharpe_per_trade,
            "insufficient_sample": n < INSUFFICIENT_SAMPLE_THRESHOLD,
        }
    return result


# ============================================================================
# Sesgos
# ============================================================================

def compute_bias_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM universe")
        n_total = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM universe WHERE is_delisted_flag")
        n_delisted = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM prices WHERE survivorship_warning")
        n_gaps = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM prices")
        n_price_rows = cur.fetchone()["n"]

    return {
        "n_total_tickers": n_total,
        "n_delisted": n_delisted,
        "survivorship_bias_pct": (n_delisted / n_total * 100) if n_total else None,
        "n_price_gaps": n_gaps,
        "n_price_rows": n_price_rows,
        "data_gap_pct": (n_gaps / n_price_rows * 100) if n_price_rows else None,
    }


def compute_asymmetry_report(trades: list[dict], threshold_pct: float = 3.0) -> dict:
    """Proxy de "¿ganar +3% es más fácil que perder -3%?": % de trades cuyo
    actual_move_pct alcanzó +threshold vs -threshold. Es un proxy, no un
    análisis de excursión máxima favorable/adversa real (MFE/MAE) — eso
    requeriría guardar el camino intradía completo de cada posición, que
    portfolio_trades no persiste (solo el resultado consolidado). Documentado,
    no oculto — mismo principio que el resto del proyecto."""
    if not trades:
        return {"n_trades": 0, "pct_reaching_positive_threshold": None, "pct_reaching_negative_threshold": None, "asymmetric_favoring_gains": None}

    moves = [float(t["actual_move_pct"]) for t in trades]
    n = len(moves)
    pct_pos = sum(1 for m in moves if m >= threshold_pct) / n * 100
    pct_neg = sum(1 for m in moves if m <= -threshold_pct) / n * 100
    return {
        "n_trades": n,
        "threshold_pct": threshold_pct,
        "pct_reaching_positive_threshold": pct_pos,
        "pct_reaching_negative_threshold": pct_neg,
        "asymmetric_favoring_gains": pct_pos > pct_neg,
    }


# ============================================================================
# Calibración predicho vs. real
# ============================================================================

def compute_calibration(trades: list[dict]) -> dict:
    """trades: dicts con 'ev' (fracción, ej. 0.005 = 0.5%) y 'actual_move_pct'
    (en %, ya en la misma escala tras *100). Fórmula EXACTA del spec:
    calibration_score = 1 - |predicted - actual| / |predicted| — se usa
    |predicted| en el denominador (no el literal `predicted`) para que el
    signo de la predicción no invierta el sentido del score si algún día es
    negativa; no está acotada en [0,1] por diseño del propio spec (puede
    salir negativa si el error es grande, o compararse contra un
    `predicted` cercano a 0 y dispararse) — se reporta tal cual, sin recortar."""
    if not trades:
        return {"n_trades": 0, "mean_predicted_pct": None, "mean_actual_pct": None, "calibration_score": None, "meets_target": None}

    predicted = np.array([float(t["ev"]) * 100 for t in trades])
    actual = np.array([float(t["actual_move_pct"]) for t in trades])
    mean_predicted = float(predicted.mean())
    mean_actual = float(actual.mean())

    if abs(mean_predicted) < 1e-9:
        calibration_score = None
    else:
        calibration_score = 1 - abs(mean_predicted - mean_actual) / abs(mean_predicted)

    return {
        "n_trades": len(trades),
        "mean_predicted_pct": mean_predicted,
        "mean_actual_pct": mean_actual,
        "calibration_score": calibration_score,
        "meets_target": (calibration_score is not None and calibration_score > CALIBRATION_TARGET),
    }


def compute_prediction_regression(trades: list[dict]) -> dict:
    """R² de actual_move_pct ~ ev, sobre trades individuales (no agregados
    por clase) — es el insumo del scatter "prediction vs actual" y su
    regresión que pide el spec como output. R² = correlación² para una
    regresión lineal simple de un solo predictor (equivalente matemático,
    evita añadir una dependencia de regresión solo para esto)."""
    if len(trades) < 2:
        return {"n_trades": len(trades), "r_squared": None, "scatter": []}

    predicted = np.array([float(t["ev"]) * 100 for t in trades])
    actual = np.array([float(t["actual_move_pct"]) for t in trades])
    scatter = [{"predicted": float(p), "actual": float(a)} for p, a in zip(predicted, actual)]

    if predicted.std() == 0 or actual.std() == 0:
        return {"n_trades": len(trades), "r_squared": None, "scatter": scatter}

    correlation = float(np.corrcoef(predicted, actual)[0, 1])
    return {"n_trades": len(trades), "r_squared": correlation**2, "scatter": scatter}


def top_n_trades(trades: list[dict], n: int = 10, winners: bool = True) -> list[dict]:
    """Top N ganadores o perdedores por pnl_pct — para las tablas de salida."""
    return sorted(trades, key=lambda t: float(t["pnl_pct"]), reverse=winners)[:n]
