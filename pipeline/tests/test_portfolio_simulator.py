"""test_portfolio_simulator.py — mecánica de posición pura (sin BD, sin red).

Cada regla del DAILY_LOOP del spec probada de forma aislada: TP, SL,
max_holding, trailing stop (incluyendo el gap que dispara varios tramos el
mismo día), y la prioridad SL-antes-que-TP cuando ambos se cruzan el mismo día.
"""
from datetime import date, timedelta

import pytest

from pipeline.backtest.portfolio_simulator import (
    COMMISSION_BPS_ROUND_TRIP,
    OpenPosition,
    compute_position_mtm_dollars,
    compute_target_date,
    compute_tp_sl_prices,
    consolidate_trade_record,
    gain_pct,
    open_position,
    step_position_forward,
)
from pipeline.backtest.portfolio_strategies import STRATEGIES, generate_trailing_stop_tiers

D0 = date(2024, 1, 2)


def _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5, trailing_tiers=None, target_days=5):
    tp, sl = compute_tp_sl_prices("LONG", entry_price, take_profit_pct, stop_loss_pct)
    return OpenPosition(
        event_id=1, version="CONSERVATIVE", execution_style="CONSERVATIVE", direction="LONG",
        ticker="TEST", entry_date=D0, entry_price=entry_price, position_size_pct=3.0,
        position_size_dollars=3000.0, target_date=D0 + timedelta(days=target_days),
        take_profit_price=tp, stop_loss_price=sl, trailing_tiers=trailing_tiers,
        confidence=80.0, ev=0.01, prediction=0.6, had_survivorship_warning=False,
    )


def _make_short_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5):
    tp, sl = compute_tp_sl_prices("SHORT", entry_price, take_profit_pct, stop_loss_pct)
    return OpenPosition(
        event_id=1, version="CONSERVATIVE", execution_style="CONSERVATIVE", direction="SHORT",
        ticker="TEST", entry_date=D0, entry_price=entry_price, position_size_pct=3.0,
        position_size_dollars=3000.0, target_date=D0 + timedelta(days=5),
        take_profit_price=tp, stop_loss_price=sl, trailing_tiers=None,
        confidence=80.0, ev=0.01, prediction=-0.6, had_survivorship_warning=False,
    )


# ---------------------------------------------------------------------------
# gain_pct / compute_tp_sl_prices
# ---------------------------------------------------------------------------


def test_gain_pct_long_and_short_are_mirrored():
    assert gain_pct("LONG", 100, 110) == pytest.approx(10.0)
    assert gain_pct("SHORT", 100, 110) == pytest.approx(-10.0)
    assert gain_pct("SHORT", 100, 90) == pytest.approx(10.0)


def test_compute_tp_sl_prices_long():
    tp, sl = compute_tp_sl_prices("LONG", 100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    assert tp == pytest.approx(102.0)
    assert sl == pytest.approx(98.5)


def test_compute_tp_sl_prices_short_are_inverted():
    tp, sl = compute_tp_sl_prices("SHORT", 100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    assert tp == pytest.approx(98.0)   # baja para dar beneficio al short
    assert sl == pytest.approx(101.5)  # sube en contra del short


def test_compute_tp_sl_prices_none_take_profit_for_trailing_strategies():
    tp, sl = compute_tp_sl_prices("LONG", 100.0, take_profit_pct=None, stop_loss_pct=5.0)
    assert tp is None
    assert sl == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# step_position_forward — cada regla aislada
# ---------------------------------------------------------------------------


def test_take_profit_triggers_on_intraday_high():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=103.0, low=99.5, close=101.0, trade_date=D0 + timedelta(days=1))
    assert pos.remaining_fraction == 0.0
    assert pos.closes[-1][3] == "TAKE_PROFIT"
    assert pos.closes[-1][2] == pytest.approx(102.0)  # se llena al precio del TP, no al close


def test_stop_loss_triggers_on_intraday_low():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=100.5, low=98.0, close=99.0, trade_date=D0 + timedelta(days=1))
    assert pos.remaining_fraction == 0.0
    assert pos.closes[-1][3] == "STOP_LOSS"
    assert pos.closes[-1][2] == pytest.approx(98.5)


def test_stop_loss_wins_when_both_tp_and_sl_cross_same_day():
    """Decisión de diseño documentada: gap grande que cruza ambos el mismo
    día -> gana el stop-loss (convención conservadora)."""
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=110.0, low=90.0, close=100.0, trade_date=D0 + timedelta(days=1))
    assert pos.closes[-1][3] == "STOP_LOSS"


def test_short_take_profit_and_stop_loss_use_correct_sides():
    pos = _make_short_position(entry_price=100.0)
    # Precio cae -> beneficio para el short -> TP (98.0) debe activarse por el LOW.
    step_position_forward(pos, high=99.5, low=97.0, close=98.0, trade_date=D0 + timedelta(days=1))
    assert pos.closes[-1][3] == "TAKE_PROFIT"


def test_hold_when_no_condition_triggers():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5, target_days=5)
    step_position_forward(pos, high=100.5, low=99.5, close=100.2, trade_date=D0 + timedelta(days=1))
    assert pos.remaining_fraction == 1.0
    assert pos.closes == []


def test_max_holding_closes_remainder_at_close_price():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5, target_days=2)
    target = pos.target_date
    step_position_forward(pos, high=100.5, low=99.8, close=100.3, trade_date=target)
    assert pos.remaining_fraction == 0.0
    assert pos.closes[-1] == (target, 1.0, 100.3, "MAX_HOLDING")


# ---------------------------------------------------------------------------
# Trailing stop (Aggressive)
# ---------------------------------------------------------------------------


def test_trailing_stop_single_tier_triggers_at_threshold_price_not_close():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers)
    step_position_forward(pos, high=125.0, low=115.0, close=120.0, trade_date=D0 + timedelta(days=3))
    assert pos.remaining_fraction == pytest.approx(0.70)  # cerró el primer tramo (30%)
    assert pos.closes[-1] == (D0 + timedelta(days=3), pytest.approx(0.30), pytest.approx(120.0), "TRAILING_STOP")


def test_trailing_stop_gap_triggers_multiple_tiers_same_day():
    tiers = generate_trailing_stop_tiers()  # (20,.3),(40,.3),(60,.3),(80,.1)
    # target_days=20: el día 10 debe seguir DENTRO del holding period, si no
    # el propio check de max_holding cerraría también el 10% restante en la
    # misma llamada (se encontró exactamente ese caso con el target_days=5
    # por defecto: el test fallaba no por un bug de trailing stop, sino
    # porque día 10 > target_date de 5 días también disparaba MAX_HOLDING).
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    # Un gap hasta +65% en un solo día cruza los tramos de 20/40/60.
    step_position_forward(pos, high=165.0, low=160.0, close=163.0, trade_date=D0 + timedelta(days=10))
    assert pos.remaining_fraction == pytest.approx(0.10)  # 3 tramos de 30% cerrados = 90%
    assert len(pos.closes) == 3
    assert [c[3] for c in pos.closes] == ["TRAILING_STOP"] * 3


def test_trailing_stop_fully_closes_across_multiple_days():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))  # +20%
    step_position_forward(pos, high=141.0, low=139.0, close=140.0, trade_date=D0 + timedelta(days=4))  # +40%
    step_position_forward(pos, high=161.0, low=159.0, close=160.0, trade_date=D0 + timedelta(days=6))  # +60%
    step_position_forward(pos, high=181.0, low=179.0, close=180.0, trade_date=D0 + timedelta(days=8))  # +80% -> cierra el resto
    assert pos.remaining_fraction == pytest.approx(0.0)
    assert len(pos.closes) == 4
    assert sum(f for _, f, _, _ in pos.closes) == pytest.approx(1.0)


def test_trailing_stop_does_not_refire_same_tier_twice():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    step_position_forward(pos, high=125.0, low=120.0, close=122.0, trade_date=D0 + timedelta(days=2))
    step_position_forward(pos, high=126.0, low=121.0, close=123.0, trade_date=D0 + timedelta(days=3))  # sigue en el mismo tramo (+20-40%)
    assert len(pos.closes) == 1  # el tramo de 20% no se vuelve a disparar


def test_stop_loss_overrides_trailing_stop_same_day():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers)
    step_position_forward(pos, high=100.5, low=94.0, close=95.0, trade_date=D0 + timedelta(days=1))
    assert pos.closes[-1][3] == "STOP_LOSS"
    assert pos.remaining_fraction == 0.0


# ---------------------------------------------------------------------------
# consolidate_trade_record
# ---------------------------------------------------------------------------


def test_consolidate_single_close_applies_commission():
    pos = _make_long_position(entry_price=100.0, take_profit_pct=2.0, stop_loss_pct=1.5)
    step_position_forward(pos, high=103.0, low=99.0, close=101.0, trade_date=D0 + timedelta(days=1))
    record = consolidate_trade_record(pos)
    assert record["actual_move_pct"] == pytest.approx(2.0)  # (102-100)/100*100
    assert record["pnl_pct"] == pytest.approx(2.0 - COMMISSION_BPS_ROUND_TRIP / 100)
    assert record["exit_reason"] == "TAKE_PROFIT"
    assert record["exit_date"] > record["entry_date"]  # checksum anti-look-ahead


def test_consolidate_weighted_average_across_trailing_tiers():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=20)
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))
    step_position_forward(pos, high=200.0, low=195.0, close=198.0, trade_date=D0 + timedelta(days=4))  # gap grande cierra el resto
    record = consolidate_trade_record(pos)
    # exit_reason debe ser TRAILING_STOP aunque el segundo cierre haya sido un gap grande
    assert record["exit_reason"] == "TRAILING_STOP"
    # precio ponderado entre el tramo de 120 (30%) y el resto a ~140-160 (70%, varios tramos)
    assert 100.0 < record["exit_price"] < 200.0


def test_consolidate_raises_assertion_if_position_still_open():
    pos = _make_long_position()
    with pytest.raises(AssertionError):
        consolidate_trade_record(pos)


def test_consolidate_exit_reason_is_trailing_stop_even_if_remainder_closes_by_max_holding():
    tiers = generate_trailing_stop_tiers()
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=tiers, target_days=5)
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))  # dispara un tramo
    step_position_forward(pos, high=121.5, low=120.5, close=121.0, trade_date=pos.target_date)  # resto por max holding
    record = consolidate_trade_record(pos)
    assert record["exit_reason"] == "TRAILING_STOP"  # el trailing stop domina, aunque el resto cerrara por tiempo


# ---------------------------------------------------------------------------
# open_position / compute_target_date
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# compute_position_mtm_dollars
# ---------------------------------------------------------------------------


def test_mtm_fully_open_position_uses_current_price():
    pos = _make_long_position(entry_price=100.0)
    pos.position_size_dollars = 1000.0
    mtm = compute_position_mtm_dollars(pos, current_price=110.0)
    assert mtm == pytest.approx(1100.0)  # +10% sobre los $1000


def test_mtm_partially_closed_position_locks_in_closed_fraction_at_its_own_price():
    """El tramo ya cerrado no debe revalorizarse con el precio de hoy — se
    encontró esto como el motivo por el que la curva de equity sobreestimaría
    el riesgo restante de un trailing stop a mitad de camino (ver docstring
    de compute_position_mtm_dollars)."""
    pos = _make_long_position(entry_price=100.0, take_profit_pct=None, stop_loss_pct=5.0, trailing_tiers=generate_trailing_stop_tiers())
    pos.position_size_dollars = 1000.0
    step_position_forward(pos, high=121.0, low=119.0, close=120.0, trade_date=D0 + timedelta(days=2))  # cierra 30% a ~120
    assert pos.remaining_fraction == pytest.approx(0.70)

    # Precio de hoy cae a 90 — el 30% ya cerrado a 120 NO debe verse afectado.
    mtm = compute_position_mtm_dollars(pos, current_price=90.0)
    expected = 1000.0 * 0.30 * 1.20 + 1000.0 * 0.70 * 0.90
    assert mtm == pytest.approx(expected)


def test_open_position_conservative_sizing_and_thresholds():
    pos = open_position(
        event_id=1, version="CONSERVATIVE", execution_style="CONSERVATIVE", direction="LONG",
        ticker="TEST", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=5),
        balance_for_sizing=100_000.0, confidence=100.0, ev=0.01, prediction=0.8, had_survivorship_warning=False,
    )
    assert pos.position_size_pct == pytest.approx(5.0)  # confidence=100 -> tope de la banda
    assert pos.position_size_dollars == pytest.approx(5000.0)
    assert pos.take_profit_price == pytest.approx(102.0)
    assert pos.stop_loss_price == pytest.approx(98.5)


def test_open_position_balanced_uses_fixed_sizing_not_interpolated():
    pos = open_position(
        event_id=1, version="BALANCED", execution_style="AGGRESSIVE", direction="LONG",
        ticker="TEST", entry_date=D0, entry_price=100.0, target_date=D0 + timedelta(days=20),
        balance_for_sizing=100_000.0, confidence=99.0, ev=0.02, prediction=0.9, had_survivorship_warning=False,
    )
    assert pos.position_size_pct == pytest.approx(5.0)  # fijo, no interpola aunque confidence sea altísima


def test_compute_target_date_counts_trading_days_not_calendar_days():
    calendar = [D0 + timedelta(days=i) for i in range(0, 15) if (D0 + timedelta(days=i)).weekday() < 5]
    entry = calendar[0]
    target = compute_target_date(entry, holding_period_max_days=5, ticker_trading_dates=calendar)
    future_trading_days = [d for d in calendar if d > entry]
    assert target == future_trading_days[4]  # el 5º día de negociación posterior


def test_compute_target_date_falls_back_to_last_available_day_when_data_runs_out():
    calendar = [D0, D0 + timedelta(days=1), D0 + timedelta(days=2)]
    target = compute_target_date(D0, holding_period_max_days=20, ticker_trading_dates=calendar)
    assert target == calendar[-1]
