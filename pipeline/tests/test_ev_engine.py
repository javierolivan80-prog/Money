"""test_ev_engine.py — Etapa 7, lógica pura, sin I/O."""
import pytest

from pipeline.analyze.ev_engine import EV_THRESHOLDS, compute_ev


def test_aggressive_has_larger_magnitude_than_conservative_same_inputs():
    """Invariante de diseño central del módulo (ver su docstring): a igualdad
    de inputs, |ev_aggressive| > |ev_balanced| > |ev_conservative|."""
    result = compute_ev(net_conviction=0.6, confidence_in_conviction=70, expected_magnitude_pct=4.0, impact_confidence=60)
    assert abs(result.ev_aggressive) > abs(result.ev_balanced) > abs(result.ev_conservative)


def test_ev_sign_follows_net_conviction():
    bullish = compute_ev(0.7, 70, 4.0, 60)
    bearish = compute_ev(-0.7, 70, 4.0, 60)
    assert bullish.ev_balanced > 0
    assert bearish.ev_balanced < 0
    assert bullish.ev_balanced == pytest.approx(-bearish.ev_balanced)


def test_zero_conviction_gives_zero_ev_regardless_of_magnitude():
    result = compute_ev(net_conviction=0.0, confidence_in_conviction=90, expected_magnitude_pct=10.0, impact_confidence=90)
    assert result.ev_conservative == 0.0
    assert result.ev_balanced == 0.0
    assert result.ev_aggressive == 0.0


def test_low_confidence_in_either_dimension_pulls_ev_toward_zero():
    """Un Judge inseguro (confidence baja) O pocos análogos (impact_confidence
    baja) deben tirar el EV hacia 0 — no basta con que uno de los dos sea alto."""
    high_both = compute_ev(0.8, 90, 5.0, 90)
    low_judge_conf = compute_ev(0.8, 20, 5.0, 90)
    low_impact_conf = compute_ev(0.8, 90, 5.0, 20)
    assert abs(low_judge_conf.ev_balanced) < abs(high_both.ev_balanced)
    assert abs(low_impact_conf.ev_balanced) < abs(high_both.ev_balanced)


def test_threshold_check_strings_match_spec_thresholds():
    # EV balanced de 2% debe superar el umbral BALANCED (0.5%) -> TRADE
    result = compute_ev(net_conviction=0.9, confidence_in_conviction=95, expected_magnitude_pct=3.0, impact_confidence=95)
    assert "TRADE" in result.threshold_balanced
    assert f"{EV_THRESHOLDS['BALANCED'] * 100:.1f}%" in result.threshold_balanced


def test_threshold_check_no_trade_below_threshold():
    result = compute_ev(net_conviction=0.05, confidence_in_conviction=30, expected_magnitude_pct=1.0, impact_confidence=30)
    assert "NO_TRADE" in result.threshold_conservative
    assert "NO_TRADE" in result.threshold_balanced
    assert "NO_TRADE" in result.threshold_aggressive


def test_position_sizing_never_exceeds_per_strategy_cap():
    # Inputs extremos a propósito: EV enorme no debe producir un tamaño de
    # posición sin límite.
    result = compute_ev(net_conviction=1.0, confidence_in_conviction=100, expected_magnitude_pct=50.0, impact_confidence=100)
    assert result.position_sizing_conservative_pct <= 3.0
    assert result.position_sizing_balanced_pct <= 5.0
    assert result.position_sizing_aggressive_pct <= 8.0


def test_position_sizing_scales_with_confidence():
    high_conf = compute_ev(0.6, 90, 4.0, 80)
    low_conf = compute_ev(0.6, 20, 4.0, 80)
    assert high_conf.position_sizing_balanced_pct > low_conf.position_sizing_balanced_pct


def test_as_json_matches_spec_field_names():
    result = compute_ev(0.5, 60, 3.0, 60)
    payload = result.as_json()
    expected_keys = {
        "ev_conservative", "ev_aggressive", "ev_balanced",
        "position_sizing_conservative", "position_sizing_aggressive", "position_sizing_balanced",
        "threshold_conservative", "threshold_aggressive", "threshold_balanced",
        "reasoning",
    }
    assert set(payload.keys()) == expected_keys
    assert payload["position_sizing_conservative"].endswith("%")
