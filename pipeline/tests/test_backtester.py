"""test_backtester.py — pruebas del motor de backtest, con foco en T1/T2/T3
de ARCHITECTURE_LEAN.md §8 (falsación, no solo tests unitarios).

Todo aquí usa datos sintéticos: no depende de red ni de EDGAR/yfinance.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline.backtest.backtester import (
    compute_car,
    compute_trade_return,
    decide_trade,
    run_backtest_for_event,
    summarize_run,
)

# ---------------------------------------------------------------------------
# Unit tests de lógica pura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ev_score,confidence,strategy,expected_direction,expected_trade",
    [
        (3.0, 0.80, "CONSERVATIVE", "LONG", True),
        (3.0, 0.60, "CONSERVATIVE", "NO_TRADE", False),   # confianza insuficiente para Conservative
        (0.5, 0.60, "CONSERVATIVE", "NO_TRADE", False),   # ev_score insuficiente para Conservative
        (0.5, 0.45, "AGGRESSIVE", "LONG", True),          # el mismo evento SÍ opera en Aggressive
        (-2.0, 0.75, "BALANCED", "SHORT", True),
    ],
)
def test_decide_trade_thresholds_per_strategy(ev_score, confidence, strategy, expected_direction, expected_trade):
    direction, should_trade = decide_trade(ev_score, confidence, strategy)
    assert direction == expected_direction
    assert should_trade == expected_trade


def test_conservative_never_trades_more_than_aggressive_on_same_inputs():
    """Invariante de diseño: Conservative es un subconjunto de lo que opera
    Aggressive, nunca al revés (mismos inputs, umbral más alto)."""
    rng = np.random.default_rng(0)
    ev_scores = rng.normal(0, 1.5, 500)
    confidences = rng.uniform(0.3, 0.9, 500)
    cons_trades = sum(decide_trade(e, c, "CONSERVATIVE")[1] for e, c in zip(ev_scores, confidences))
    aggr_trades = sum(decide_trade(e, c, "AGGRESSIVE")[1] for e, c in zip(ev_scores, confidences))
    assert cons_trades <= aggr_trades


def test_compute_trade_return_long_vs_short_are_mirrored():
    long_ret = compute_trade_return(100, 110, "LONG", slippage_bps=0)
    short_ret = compute_trade_return(100, 110, "SHORT", slippage_bps=0)
    assert long_ret == pytest.approx(10.0)
    assert short_ret == pytest.approx(-10.0)


def test_compute_trade_return_slippage_reduces_magnitude():
    no_slip = compute_trade_return(100, 110, "LONG", slippage_bps=0)
    with_slip = compute_trade_return(100, 110, "LONG", slippage_bps=50)
    assert with_slip < no_slip
    assert with_slip == pytest.approx(no_slip - 1.0)  # 50bps*2 patas = 1.0%


def test_compute_trade_return_edge_survives_25bps_or_dies_as_designed():
    """T7 de la arquitectura: el edge debe evaluarse a 0/10/25/50bps. Aquí se
    verifica solo que el barrido está bien calculado (edge decrece monótonamente
    con el slippage) — la pregunta de si SOBREVIVE es empírica, no de este test."""
    returns_by_slippage = [compute_trade_return(100, 100.3, "LONG", bps) for bps in [0, 10, 25, 50]]
    assert returns_by_slippage == sorted(returns_by_slippage, reverse=True)


# ---------------------------------------------------------------------------
# T3 — auditoría de look-ahead (ARCHITECTURE_LEAN.md §8)
# ---------------------------------------------------------------------------


def _synthetic_trading_calendar(start: date, n_days: int) -> list[date]:
    dates = []
    d = start
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def test_entry_date_is_always_strictly_after_d0():
    """El assert anti-look-ahead en run_backtest_for_event debe cumplirse
    siempre que haya suficientes días futuros — se prueba con 50 eventos
    sintéticos en fechas distintas, incluyendo viernes (cruce de fin de semana)."""
    calendar = _synthetic_trading_calendar(date(2021, 1, 4), 300)
    prices = {d: 100 + i * 0.1 for i, d in enumerate(calendar)}

    for i in range(0, 250, 5):
        d0 = calendar[i]
        trade = run_backtest_for_event(
            event_id=i,
            d0_close_date=d0,
            ev_score=2.5,
            judge_confidence=0.8,
            strategy_version="CONSERVATIVE",
            prices_by_date=prices,
            trading_dates_sorted=calendar,
            window_days=5,
            slippage_bps=25,
        )
        assert trade is not None
        assert trade.entry_date > d0
        assert trade.exit_date > trade.entry_date


def test_shifting_event_date_by_one_day_changes_which_prices_are_used():
    """Segunda mitad de T3: desplazar la fecha del evento +1 día debe cambiar
    el trade resultante (distinta entry/exit), porque si no cambiara, el
    backtest no estaría realmente ligado a la fecha del evento."""
    calendar = _synthetic_trading_calendar(date(2021, 1, 4), 60)
    prices = {d: 100 + i for i, d in enumerate(calendar)}  # precio distinto cada día, a propósito

    trade_a = run_backtest_for_event(
        1, calendar[10], 2.0, 0.8, "BALANCED", prices, calendar, window_days=5, slippage_bps=0
    )
    trade_b = run_backtest_for_event(
        1, calendar[11], 2.0, 0.8, "BALANCED", prices, calendar, window_days=5, slippage_bps=0
    )
    assert trade_a.entry_date != trade_b.entry_date
    assert trade_a.realized_return_pct != trade_b.realized_return_pct


# ---------------------------------------------------------------------------
# T1 — placebo con fechas aleatorias (ARCHITECTURE_LEAN.md §8, BLOQUEANTE)
# ---------------------------------------------------------------------------


def test_placebo_random_dates_produce_zero_mean_edge():
    """Corre el motor de trade completo sobre 300 eventos 'falsos' con
    ev_score y dirección asignados AL AZAR (sin relación con el retorno real,
    que también es ruido browniano puro). El resultado agregado debe ser
    estadísticamente indistinguible de cero.

    Si este test falla, el motor de cálculo de retorno o el motor de decisión
    tiene un sesgo estructural y ningún resultado posterior del sistema es de
    fiar (ARCHITECTURE_LEAN.md §8: T1 es bloqueante)."""
    rng = np.random.default_rng(123)
    calendar = _synthetic_trading_calendar(date(2021, 1, 4), 1500)
    # Precio = paseo aleatorio puro, sin ninguna deriva ligada a ev_score.
    log_returns = rng.normal(0, 0.02, len(calendar))
    price_series = 100 * np.exp(np.cumsum(log_returns))
    prices = dict(zip(calendar, price_series))

    trades = []
    for i in range(300):
        idx = rng.integers(50, len(calendar) - 50)
        d0 = calendar[idx]
        ev_score = rng.normal(0, 2.0)  # sin ninguna relación con el precio futuro
        trade = run_backtest_for_event(
            event_id=i,
            d0_close_date=d0,
            ev_score=ev_score,
            judge_confidence=rng.uniform(0.4, 0.9),
            strategy_version="AGGRESSIVE",  # umbral bajo para maximizar n de trades del placebo
            prices_by_date=prices,
            trading_dates_sorted=calendar,
            window_days=20,
            slippage_bps=0,  # sin slippage: se quiere aislar el sesgo del MOTOR, no de costes
        )
        if trade:
            trades.append(trade)

    assert len(trades) > 50, "Muestra de placebo demasiado pequeña para el test de significancia"
    summary = summarize_run(trades)
    # El IC de 95% del retorno medio debe contener 0. Si no lo contiene, hay
    # un sesgo estructural en compute_trade_return o en la selección de
    # entry/exit — y hay que arreglarlo antes de interpretar CUALQUIER resultado.
    assert summary["return_ci95_low"] <= 0 <= summary["return_ci95_high"], (
        f"PLACEBO FALLIDO: el IC95%% [{summary['return_ci95_low']:.3f}, "
        f"{summary['return_ci95_high']:.3f}] no contiene cero. El motor de "
        f"backtest tiene un sesgo estructural — no interpretar ningún resultado "
        f"real hasta corregirlo (T1, ARCHITECTURE_LEAN.md §8, bloqueante)."
    )


# ---------------------------------------------------------------------------
# T2 — réplica de un efecto conocido (ARCHITECTURE_LEAN.md §8, BLOQUEANTE)
# ---------------------------------------------------------------------------


def test_compute_car_replicates_known_effect_direction_and_significance():
    """Réplica simplificada de un resultado de manual: un evento con retorno
    anormal negativo persistente en la ventana [D+1,D+20] (simulando la
    literatura de restatements/Item 4.02, CAR típico -5% a -10%) debe producir
    un CAR negativo y de magnitud coherente cuando se le inyecta una deriva
    negativa clara por encima del ruido de los factores.

    Esto NO reemplaza la réplica real contra datos de mercado del día 5 del
    plan (ARCHITECTURE_LEAN.md §9) — esa exige datos reales de EDGAR/yfinance,
    que este sandbox no puede descargar. Esto prueba que el MOTOR de CAR
    (compute_car) recupera correctamente una señal conocida inyectada en datos
    sintéticos, que es la parte que sí se puede validar sin red."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-04", periods=320, freq="B")

    factor_returns = pd.DataFrame(
        {
            "mkt_rf": rng.normal(0.0003, 0.01, len(dates)),
            "smb": rng.normal(0.0, 0.005, len(dates)),
            "hml": rng.normal(0.0, 0.005, len(dates)),
            "rf": np.full(len(dates), 0.00005),
        },
        index=dates,
    )

    # Sensibilidad de la acción a los factores + ruido idiosincrático.
    beta_mkt, beta_smb, beta_hml = 1.1, 0.3, -0.2
    idio_noise = rng.normal(0, 0.008, len(dates))
    stock_ret = (
        factor_returns["rf"]
        + beta_mkt * factor_returns["mkt_rf"]
        + beta_smb * factor_returns["smb"]
        + beta_hml * factor_returns["hml"]
        + idio_noise
    )

    d0 = dates[280].date()
    # Inyecta la deriva negativa conocida SOLO en la ventana de evento [D+1,D+20]:
    # -7% acumulado repartido en 20 días, imitando el CAR de restatement de la
    # literatura (-5% a -10%).
    event_mask = (dates > pd.Timestamp(d0)) & (dates <= pd.Timestamp(d0) + pd.Timedelta(days=20))
    stock_ret = stock_ret.copy()
    stock_ret[event_mask] += -0.07 / event_mask.sum()

    event_prices = pd.DataFrame({"ret": stock_ret}, index=dates)

    result = compute_car(event_prices, factor_returns, d0, window_days=20)

    assert result is not None
    assert result.car < 0, "El CAR debe ser negativo: se inyectó una deriva negativa conocida"
    # Magnitud coherente con la literatura de restatements (-5% a -10%), con
    # margen amplio porque es una única simulación, no un promedio de muchas.
    assert -0.15 < result.car < -0.02, f"CAR={result.car:.4f} fuera del rango esperado para el efecto inyectado"


def test_compute_car_returns_none_when_estimation_window_too_short():
    """Un evento sin suficiente histórico de estimación debe devolver None,
    NUNCA un CAR silenciosamente calculado sobre pocos datos (que sería ruido
    disfrazado de señal)."""
    dates = pd.date_range("2021-01-04", periods=20, freq="B")  # muy por debajo del mínimo de 60
    factor_returns = pd.DataFrame(
        {"mkt_rf": [0.001] * 20, "smb": [0.0] * 20, "hml": [0.0] * 20, "rf": [0.0] * 20}, index=dates
    )
    event_prices = pd.DataFrame({"ret": [0.001] * 20}, index=dates)
    result = compute_car(event_prices, factor_returns, dates[-1].date(), window_days=5)
    assert result is None
