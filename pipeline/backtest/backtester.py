"""backtester.py — motor de event study + backtest de 3 versiones de estrategia.

DOS NIVELES DE VALIDACIÓN, y no deben confundirse (AUDIT_LEAN.md §2.2.3):
  - `compute_car()`: event study clásico (CAR vs Fama-French 3), sobre TODOS
    los eventos de una clase (n en miles/cientos). Responde "¿existe un efecto?".
  - `run_backtest()`: sobre el subconjunto que el motor de análisis decide
    operar (n en cientos), con slippage y reglas de entrada/salida. Responde
    "¿es operable?". El resultado del backtest NUNCA se usa para afirmar
    significancia estadística del efecto — para eso está compute_car().

REGLA DE ORO ANTI-LOOK-AHEAD (ARCHITECTURE_LEAN.md §4, §8 T3):
  entry_date es SIEMPRE > d0_close_date. Se aplica en tres capas independientes
  a propósito (defensa en profundidad, no redundancia perezosa):
    1. Aquí, con un assert que aborta el proceso si se viola.
    2. En el schema SQL, con CHECK constraints (chk_no_lookahead_5d/20d).
    3. En T3 (test_backtester_no_lookahead.py), desplazando fechas +1 y
       verificando que el resultado cambia en la dirección esperada.

TRES VERSIONES DE ESTRATEGIA (pedidas por el spec), que solo difieren en el
UMBRAL de EV para decidir si se opera, no en la lógica de entrada/salida:
  - CONSERVATIVE: exige ev_score alto y judge_confidence alta -> menos trades,
    mayor precisión esperada.
  - AGGRESSIVE: umbral bajo -> más trades, precisión esperada menor.
  - BALANCED: punto medio.
Esto es deliberado y simple a propósito: con n de cientos (ver AUDIT_LEAN.md
§2.2.3), variar más de un parámetro por versión generaría celdas demasiado
pequeñas para comparar las 3 versiones con algún rigor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import statsmodels.api as sm

from pipeline.backtest.factor_model import fit_factor_model

logger = logging.getLogger(__name__)

STRATEGY_THRESHOLDS = {
    # (ev_score mínimo, judge_confidence mínima)
    "CONSERVATIVE": (2.0, 0.70),
    "BALANCED": (1.0, 0.55),
    "AGGRESSIVE": (0.3, 0.40),
}


@dataclass
class CAREstimate:
    event_id: int
    window_days: int
    car: float
    abnormal_volume_ratio: float
    n_estimation_days: int


def compute_car(
    event_prices: pd.DataFrame,
    factor_returns: pd.DataFrame,
    d0_close_date: date,
    window_days: int,
    estimation_window: tuple[int, int] = (-250, -30),
) -> CAREstimate | None:
    """Calcula el retorno anormal acumulado (CAR) vs. modelo de mercado FF3.

    event_prices: DataFrame indexado por fecha con columna 'ret' (retorno diario
        simple, YA calculado por el caller a partir de close_raw*adj_factor).
    factor_returns: DataFrame indexado por fecha con columnas mkt_rf, smb, hml, rf.

    Metodología estándar de event study (ARCHITECTURE_LEAN.md §3):
      1. Regresión OLS de (ret - rf) contra (mkt_rf, smb, hml) en la ventana de
         estimación [-250, -30] respecto a D0.
      2. Retorno esperado en la ventana de evento = predicción del modelo.
      3. Retorno anormal = retorno real - retorno esperado.
      4. CAR = suma de retornos anormales en [D+1, D+window_days].

    Devuelve None si no hay suficientes días de estimación (mínimo 60, umbral
    conservador para que la regresión no sea puro ruido) — un None debe
    tratarse como "no evaluable", NUNCA como CAR=0.
    """
    merged = event_prices.join(factor_returns, how="inner")
    fit = fit_factor_model(merged, d0_close_date, estimation_window)
    if fit is None:
        return None

    event_end = pd.Timestamp(d0_close_date) + pd.Timedelta(days=window_days)
    event_data = merged[(merged.index > pd.Timestamp(d0_close_date)) & (merged.index <= event_end)]
    if event_data.empty:
        return None

    X_event = sm.add_constant(event_data[["mkt_rf", "smb", "hml"]], has_constant="add")
    expected_ret = fit.model.predict(X_event) + event_data["rf"]
    abnormal_ret = event_data["ret"] - expected_ret
    car = abnormal_ret.sum()

    baseline_vol = merged["ret"].rolling(60).std().iloc[-1] if "ret" in merged else np.nan
    event_vol = event_data["ret"].std() if len(event_data) > 1 else np.nan
    abnormal_volume_ratio = (event_vol / baseline_vol) if baseline_vol and baseline_vol > 0 else np.nan

    return CAREstimate(
        event_id=-1,  # el caller lo rellena
        window_days=window_days,
        car=float(car),
        abnormal_volume_ratio=float(abnormal_volume_ratio) if not np.isnan(abnormal_volume_ratio) else None,
        n_estimation_days=fit.n_estimation_days,
    )


def decide_trade(ev_score: float, judge_confidence: float, strategy_version: str) -> tuple[str, bool]:
    """Decide dirección (LONG/SHORT/NO_TRADE) y si la versión de estrategia
    opera este evento, según su umbral. Pura, sin estado — fácil de testear."""
    min_ev, min_conf = STRATEGY_THRESHOLDS[strategy_version]
    if abs(ev_score) < min_ev or judge_confidence < min_conf:
        return "NO_TRADE", False
    return ("LONG" if ev_score > 0 else "SHORT"), True


def compute_trade_return(
    entry_price: float,
    exit_price: float,
    direction: str,
    slippage_bps: float,
) -> float:
    """Retorno realizado de un trade, en %, con slippage aplicado en ambas patas
    (entrada y salida) — T7 de ARCHITECTURE_LEAN.md exige barrido de sensibilidad,
    así que slippage_bps es un parámetro, no una constante."""
    raw_return = (exit_price - entry_price) / entry_price
    if direction == "SHORT":
        raw_return = -raw_return
    slippage_frac = (slippage_bps / 10_000) * 2  # una vez en entrada, una vez en salida
    return (raw_return - slippage_frac) * 100


@dataclass
class BacktestTrade:
    event_id: int
    strategy_version: str
    entry_date: date
    exit_date: date
    predicted_direction: str
    predicted_ev_pct: float
    realized_return_pct: float
    hit: bool
    slippage_bps_applied: float


def run_backtest_for_event(
    event_id: int,
    d0_close_date: date,
    ev_score: float,
    judge_confidence: float,
    strategy_version: str,
    prices_by_date: dict[date, float],
    trading_dates_sorted: list[date],
    window_days: int,
    slippage_bps: float,
) -> BacktestTrade | None:
    """Ejecuta un trade simulado para UN evento y UNA versión de estrategia.

    ANTI-LOOK-AHEAD: entry_date es el primer día de negociación
    ESTRICTAMENTE POSTERIOR a d0_close_date (apertura de D+1, conservador
    per ARCHITECTURE_LEAN.md §2.5 / §10'). Se afirma con `assert` porque una
    violación aquí invalidaría cualquier resultado del backtest — no es un
    caso a tolerar silenciosamente.
    """
    direction, should_trade = decide_trade(ev_score, judge_confidence, strategy_version)
    if not should_trade:
        return None

    future_dates = [d for d in trading_dates_sorted if d > d0_close_date]
    if len(future_dates) < window_days:
        logger.warning("Evento %d: no hay suficientes días futuros de cotización, se omite", event_id)
        return None

    entry_date = future_dates[0]
    exit_date = future_dates[window_days - 1]

    assert entry_date > d0_close_date, (
        f"VIOLACIÓN ANTI-LOOK-AHEAD: entry_date {entry_date} no es posterior a "
        f"d0_close_date {d0_close_date} para el evento {event_id}. Esto NO debe "
        f"ocurrir nunca — aborta el proceso en vez de producir un resultado inválido."
    )

    entry_price = prices_by_date.get(entry_date)
    exit_price = prices_by_date.get(exit_date)
    if entry_price is None or exit_price is None:
        logger.warning("Evento %d: falta precio de entrada/salida (posible gap de supervivencia)", event_id)
        return None

    realized = compute_trade_return(entry_price, exit_price, direction, slippage_bps)
    predicted_sign_positive = ev_score > 0
    realized_sign_positive = realized > 0
    hit = predicted_sign_positive == realized_sign_positive

    return BacktestTrade(
        event_id=event_id,
        strategy_version=strategy_version,
        entry_date=entry_date,
        exit_date=exit_date,
        predicted_direction=direction,
        predicted_ev_pct=ev_score,
        realized_return_pct=realized,
        hit=hit,
        slippage_bps_applied=slippage_bps,
    )


def summarize_run(trades: list[BacktestTrade]) -> dict:
    """Métricas agregadas de una corrida: win rate, Sharpe, max drawdown,
    calibración (correlación predicho vs. realizado). Con IC bootstrap
    (ARCHITECTURE_LEAN.md §8 T8) — nunca se reporta un punto sin su intervalo."""
    if not trades:
        return {"n_trades": 0}

    returns = np.array([t.realized_return_pct for t in trades])
    predicted = np.array([t.predicted_ev_pct for t in trades])
    hits = np.array([t.hit for t in trades])

    win_rate = hits.mean()
    sharpe = (returns.mean() / returns.std() * np.sqrt(252 / 20)) if returns.std() > 0 else np.nan
    cum = np.cumsum(returns)
    running_max = np.maximum.accumulate(cum)
    max_drawdown = float((cum - running_max).min())
    calibration = float(np.corrcoef(predicted, returns)[0, 1]) if len(trades) > 1 else np.nan

    rng = np.random.default_rng(42)  # semilla fija: T9 reproducibilidad
    boot_means = [rng.choice(returns, size=len(returns), replace=True).mean() for _ in range(2000)]
    ci_low, ci_high = np.percentile(boot_means, [2.5, 97.5])

    return {
        "n_trades": len(trades),
        "win_rate": float(win_rate),
        "mean_return_pct": float(returns.mean()),
        "return_ci95_low": float(ci_low),
        "return_ci95_high": float(ci_high),
        "sharpe_annualized": float(sharpe) if not np.isnan(sharpe) else None,
        "max_drawdown_pct": max_drawdown,
        "calibration_corr": calibration if not np.isnan(calibration) else None,
    }
