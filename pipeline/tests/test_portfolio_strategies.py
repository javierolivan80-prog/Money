"""test_portfolio_strategies.py — configuración pura, sin I/O."""
import pytest

from pipeline.backtest.portfolio_strategies import (
    BALANCED_EV_THRESHOLD,
    STRATEGIES,
    classify_balanced_execution_style,
    compute_balanced_position_size_pct,
    compute_position_size_pct,
    generate_trailing_stop_tiers,
)


def test_strategy_configs_match_spec_literal_values():
    cons = STRATEGIES["CONSERVATIVE"]
    assert (cons.position_size_min_pct, cons.position_size_max_pct) == (2.0, 5.0)
    assert cons.holding_period_max_days == 5
    assert cons.stop_loss_pct == 1.5
    assert cons.confidence_threshold == 70.0
    assert cons.ev_threshold == pytest.approx(0.002)
    assert cons.max_concurrent == 3

    aggr = STRATEGIES["AGGRESSIVE"]
    assert (aggr.position_size_min_pct, aggr.position_size_max_pct) == (10.0, 20.0)
    assert aggr.holding_period_max_days == 20
    assert aggr.stop_loss_pct == 5.0
    assert aggr.confidence_threshold == 50.0
    assert aggr.ev_threshold == pytest.approx(0.005)
    assert aggr.max_concurrent == 2
    assert aggr.take_profit_pct is None  # usa trailing stop, no TP fijo


def test_balanced_threshold_is_the_average_of_the_other_two():
    assert BALANCED_EV_THRESHOLD == pytest.approx((0.002 + 0.005) / 2)


# ---------------------------------------------------------------------------
# generate_trailing_stop_tiers
# ---------------------------------------------------------------------------


def test_trailing_stop_tiers_default_pattern_matches_spec_example():
    tiers = generate_trailing_stop_tiers()
    # +20% -> cierra 30%, +40% -> cierra 30% más (según el spec literalmente)
    assert tiers[0] == (20.0, pytest.approx(0.30))
    assert tiers[1] == (40.0, pytest.approx(0.30))


def test_trailing_stop_tiers_sum_to_exactly_full_position():
    tiers = generate_trailing_stop_tiers()
    assert sum(fraction for _, fraction in tiers) == pytest.approx(1.0)


def test_trailing_stop_tiers_last_tier_closes_remainder_not_a_full_30pct():
    tiers = generate_trailing_stop_tiers(increment_pct=20.0, close_fraction=0.30)
    # 30% + 30% + 30% = 90%, el cuarto tramo (+80%) cierra el 10% restante, no 30%.
    assert len(tiers) == 4
    assert tiers[3] == (80.0, pytest.approx(0.10))


def test_trailing_stop_tiers_thresholds_increase_monotonically():
    tiers = generate_trailing_stop_tiers()
    thresholds = [t for t, _ in tiers]
    assert thresholds == sorted(thresholds)
    assert len(set(thresholds)) == len(thresholds)  # sin duplicados


# ---------------------------------------------------------------------------
# compute_position_size_pct
# ---------------------------------------------------------------------------


def test_position_size_at_threshold_confidence_gives_minimum():
    cons = STRATEGIES["CONSERVATIVE"]
    size = compute_position_size_pct(confidence=cons.confidence_threshold, config=cons)
    assert size == pytest.approx(cons.position_size_min_pct)


def test_position_size_at_max_confidence_gives_maximum():
    cons = STRATEGIES["CONSERVATIVE"]
    size = compute_position_size_pct(confidence=100.0, config=cons)
    assert size == pytest.approx(cons.position_size_max_pct)


def test_position_size_scales_monotonically_with_confidence():
    cons = STRATEGIES["CONSERVATIVE"]
    low = compute_position_size_pct(confidence=75.0, config=cons)
    high = compute_position_size_pct(confidence=95.0, config=cons)
    assert cons.position_size_min_pct <= low < high <= cons.position_size_max_pct


def test_position_size_never_exceeds_band_even_below_threshold():
    """Un caso límite (confidence por debajo del umbral, no debería llegar
    aquí en la práctica porque abstention_engine ya lo habría descartado,
    pero la función debe seguir siendo segura si se llama igual)."""
    cons = STRATEGIES["CONSERVATIVE"]
    size = compute_position_size_pct(confidence=10.0, config=cons)
    assert size == pytest.approx(cons.position_size_min_pct)  # se clampa, no extrapola por debajo del mínimo


# ---------------------------------------------------------------------------
# classify_balanced_execution_style
# ---------------------------------------------------------------------------


def test_balanced_high_confidence_and_ev_gets_conservative_style():
    style = classify_balanced_execution_style(confidence=80.0, ev_conservative=0.01, ev_aggressive=0.01)
    assert style == "CONSERVATIVE"


def test_balanced_lower_confidence_but_qualifying_ev_gets_aggressive_style():
    style = classify_balanced_execution_style(confidence=55.0, ev_conservative=0.001, ev_aggressive=0.01)
    assert style == "AGGRESSIVE"


def test_balanced_conservative_wins_when_both_qualify():
    style = classify_balanced_execution_style(confidence=90.0, ev_conservative=0.02, ev_aggressive=0.02)
    assert style == "CONSERVATIVE"


def test_balanced_position_size_is_the_fixed_spec_value_not_interpolated():
    assert compute_balanced_position_size_pct("CONSERVATIVE") == pytest.approx(1.5)
    assert compute_balanced_position_size_pct("AGGRESSIVE") == pytest.approx(5.0)
