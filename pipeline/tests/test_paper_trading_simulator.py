"""test_paper_trading_simulator.py — mecánica pura (simulate_single_paper_trade)
con cálculo a mano, y selección de semana. Los tests de integración contra
Postgres real (fetch_events_for_week, simulate_paper_trading_week de
extremo a extremo) viven en test_paper_trading_simulator_integration.py."""
from datetime import date, timedelta

import pytest

from pipeline.paper_trading.simulator import (
    select_simulation_week,
    simulate_single_paper_trade,
)


def _bar(o=None, h=None, l=None, c=None):
    return {"open_raw": o, "high_raw": h, "low_raw": l, "close_raw": c}


CONS_PLAN = {"event_id": 1, "ticker": "T", "direction": "LONG", "execution_style": "CONSERVATIVE", "ev": 0.01, "confidence": 80.0, "prediction": 0.5, "version": "CONSERVATIVE"}


# ---------------------------------------------------------------------------
# select_simulation_week
# ---------------------------------------------------------------------------


def test_select_simulation_week_picks_last_full_mon_fri_before_current_week():
    # Miércoles 2025-09-17 -> semana en curso empieza lunes 2025-09-15,
    # así que la última semana COMPLETA es 2025-09-08 (lun) a 2025-09-12 (vie).
    as_of = date(2025, 9, 17)
    start, end = select_simulation_week(as_of)
    assert start == date(2025, 9, 8)
    assert end == date(2025, 9, 12)
    assert start.weekday() == 0  # lunes
    assert end.weekday() == 4  # viernes


def test_select_simulation_week_monday_still_uses_previous_week():
    # Si hoy es lunes, la semana en curso (que empieza hoy) no cuenta como
    # "completa" — se usa la semana anterior entera.
    as_of = date(2025, 9, 15)  # lunes
    start, end = select_simulation_week(as_of)
    assert end == date(2025, 9, 12)
    assert start == date(2025, 9, 8)


# ---------------------------------------------------------------------------
# simulate_single_paper_trade — take-profit
# ---------------------------------------------------------------------------


def test_paper_trade_hits_take_profit_conservative():
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 12)
    # Conservative LONG: TP a +2% = 102.0, SL a -1.5% = 98.5
    prices = {
        date(2024, 1, 3): _bar(h=101.0, l=99.5, c=100.5),
        date(2024, 1, 4): _bar(h=102.5, l=101.0, c=102.0),  # cruza TP
        date(2024, 1, 5): _bar(h=105.0, l=103.0, c=104.0),
    }
    result = simulate_single_paper_trade(prices, CONS_PLAN, entry_date, entry_price, week_end)
    assert result["status"] == "CLOSED_TP"
    assert result["exit_reason"] == "TAKE_PROFIT"
    assert result["exit_date"] == date(2024, 1, 4)
    assert result["pnl_pct"] == pytest.approx(2.0 - 0.10, abs=1e-9)  # 2% - comisión 10bps


def test_paper_trade_hits_stop_loss():
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 12)
    prices = {
        date(2024, 1, 3): _bar(h=100.5, l=98.0, c=98.5),  # cruza SL (98.5)
    }
    result = simulate_single_paper_trade(prices, CONS_PLAN, entry_date, entry_price, week_end)
    assert result["status"] == "CLOSED_SL"
    assert result["exit_reason"] == "STOP_LOSS"


def test_paper_trade_closes_timeout_at_week_end_when_neither_hit():
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 5)
    prices = {
        date(2024, 1, 3): _bar(h=100.3, l=99.8, c=100.1),
        date(2024, 1, 4): _bar(h=100.4, l=99.9, c=100.2),
        date(2024, 1, 5): _bar(h=100.5, l=100.0, c=100.3),  # semana termina aquí, sin TP/SL
        date(2024, 1, 8): _bar(h=110.0, l=109.0, c=109.5),  # dato futuro FUERA de la semana — no debe usarse
    }
    result = simulate_single_paper_trade(prices, CONS_PLAN, entry_date, entry_price, week_end)
    assert result["status"] == "CLOSED_TIMEOUT"
    assert result["exit_reason"] == "TIMEOUT"
    assert result["exit_date"] == date(2024, 1, 5)
    assert result["exit_price"] == pytest.approx(100.3)


def test_paper_trade_stays_open_when_price_data_not_yet_available_through_week_end():
    """Si el panel de precios TERMINA antes de week_end (datos aún no
    llegados — el caso real de "hoy" corriendo contra una semana que no ha
    terminado), la posición debe quedar OPEN, no forzarse a TIMEOUT."""
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 12)  # semana completa hasta el 12
    prices = {
        date(2024, 1, 3): _bar(h=100.3, l=99.8, c=100.1),  # solo tenemos datos hasta el 3 de enero
    }
    result = simulate_single_paper_trade(prices, CONS_PLAN, entry_date, entry_price, week_end)
    assert result["status"] == "OPEN"
    assert result["exit_date"] is None
    assert result["pnl_pct"] is None


def test_paper_trade_aggressive_take_profit_uses_first_trailing_tier_as_standin():
    """Aggressive no tiene take_profit_pct fijo (usa trailing stop) — en
    paper trading (sin tramos) se usa el primer umbral del trailing
    (+20%) como TP único. Ver docstring del módulo."""
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 30)
    plan = {**CONS_PLAN, "execution_style": "AGGRESSIVE"}
    prices = {
        date(2024, 1, 3): _bar(h=121.0, l=119.0, c=120.0),  # +21% > +20% -> dispara el estand-in de TP
    }
    result = simulate_single_paper_trade(prices, plan, entry_date, entry_price, week_end)
    assert result["status"] == "CLOSED_TP"
    assert result["exit_reason"] == "TAKE_PROFIT"


def test_paper_trade_short_direction_gain_pct_sign():
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 12)
    plan = {**CONS_PLAN, "direction": "SHORT"}
    # SHORT: TP a -2% = 98.0, SL a +1.5% = 101.5
    prices = {
        date(2024, 1, 3): _bar(h=99.0, l=97.5, c=98.0),  # low cruza TP de SHORT (98.0)
    }
    result = simulate_single_paper_trade(prices, plan, entry_date, entry_price, week_end)
    assert result["status"] == "CLOSED_TP"


def test_paper_trade_no_lookahead_exit_date_after_entry_date():
    entry_date = date(2024, 1, 2)
    entry_price = 100.0
    week_end = date(2024, 1, 12)
    prices = {date(2024, 1, 3): _bar(h=99.0, l=97.5, c=98.0)}
    result = simulate_single_paper_trade(prices, CONS_PLAN, entry_date, entry_price, week_end)
    assert result["exit_date"] > entry_date
