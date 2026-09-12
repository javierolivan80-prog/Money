"""test_abstention_engine.py — Etapa 8. Cada una de las 7 reglas del spec
probada de forma aislada (todas las demás condiciones "limpias"), más los
casos combinados que importan: la divergencia entre versiones de estrategia,
y el orden determinista de reason_if_no_trade cuando varias reglas disparan
a la vez.
"""
import pytest

from pipeline.analyze.abstention_engine import (
    CONFIDENCE_FLOOR,
    NOVELTY_FLOOR,
    SPREAD_CEILING_PCT,
    AbstentionInputs,
    decide_all_strategies,
    decide_for_strategy,
)
from pipeline.analyze.ev_engine import EV_THRESHOLDS


def _clean_inputs(**overrides) -> AbstentionInputs:
    """Inputs que NO disparan ninguna de las 7 reglas — cada test override
    exactamente el campo que quiere probar."""
    base = dict(
        novelty_score=80.0,
        confidence_in_conviction=90.0,
        net_conviction=0.7,
        ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": 0.05, "AGGRESSIVE": 0.05},  # muy por encima de todos los umbrales
        had_survivorship_warning=False,
        beta_available=True,
        high_low_range_pct=0.1,
        is_fda_crl_without_8k=False,
    )
    base.update(overrides)
    return AbstentionInputs(**base)


def test_clean_inputs_produce_a_trade_not_abstention():
    decision = decide_for_strategy(_clean_inputs(), "BALANCED")
    assert decision.trade_decision in ("LONG", "SHORT")
    assert decision.reason_if_no_trade is None


# --- Las 7 reglas, cada una aislada ---


def test_rule_1_novelty_below_floor():
    decision = decide_for_strategy(_clean_inputs(novelty_score=NOVELTY_FLOOR - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "novelty_score" in decision.reason_if_no_trade


def test_rule_1_novelty_at_floor_does_not_trigger():
    """Boundary: exactamente en el floor NO debe disparar (spec: '< 20', no '<= 20')."""
    decision = decide_for_strategy(_clean_inputs(novelty_score=NOVELTY_FLOOR), "BALANCED")
    assert decision.trade_decision != "NO_TRADE" or "novelty_score" not in (decision.reason_if_no_trade or "")


def test_rule_2_confidence_below_floor():
    decision = decide_for_strategy(_clean_inputs(confidence_in_conviction=CONFIDENCE_FLOOR - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "confidence_in_conviction" in decision.reason_if_no_trade


def test_rule_3_ev_below_buffered_threshold_per_strategy():
    # EV justo por encima del umbral crudo de Balanced pero por debajo del
    # umbral + 50bps de buffer -> debe abstenerse.
    just_above_raw = EV_THRESHOLDS["BALANCED"] + 0.0010  # +10bps sobre el umbral crudo, insuficiente con el buffer de 50bps
    decision = decide_for_strategy(_clean_inputs(ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": just_above_raw, "AGGRESSIVE": 0.05}), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "ev=" in decision.reason_if_no_trade


def test_rule_3_ev_above_buffered_threshold_trades():
    comfortably_above = EV_THRESHOLDS["BALANCED"] + 0.0200
    decision = decide_for_strategy(_clean_inputs(ev_by_strategy={"CONSERVATIVE": 0.05, "BALANCED": comfortably_above, "AGGRESSIVE": 0.05}), "BALANCED")
    assert decision.trade_decision in ("LONG", "SHORT")


def test_rule_4_survivorship_warning():
    decision = decide_for_strategy(_clean_inputs(had_survivorship_warning=True), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "deslistado" in decision.reason_if_no_trade


def test_rule_5_contradictory_data_factor_model_unavailable():
    decision = decide_for_strategy(_clean_inputs(beta_available=False), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "contradictorios" in decision.reason_if_no_trade


def test_rule_5_contradictory_data_judge_split_decision():
    decision = decide_for_strategy(_clean_inputs(net_conviction=0.05, confidence_in_conviction=75), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "contradictorios" in decision.reason_if_no_trade


def test_rule_5_low_conviction_alone_is_not_contradictory_if_confidence_also_low():
    """Confirma que la regla 5 exige AMBAS condiciones (conviction baja Y
    confidence alta) — conviction baja con confidence también baja debe
    disparar la regla 2 (confidence floor), no la 5."""
    decision = decide_for_strategy(_clean_inputs(net_conviction=0.05, confidence_in_conviction=CONFIDENCE_FLOOR - 1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "confidence_in_conviction" in decision.reason_if_no_trade  # regla 2, no regla 5


def test_rule_6_fda_crl_without_8k():
    decision = decide_for_strategy(_clean_inputs(is_fda_crl_without_8k=True), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "CRL" in decision.reason_if_no_trade


def test_rule_7_spread_proxy_above_ceiling():
    decision = decide_for_strategy(_clean_inputs(high_low_range_pct=SPREAD_CEILING_PCT + 0.1), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"
    assert "ilíquido" in decision.reason_if_no_trade


def test_rule_7_missing_spread_data_abstains_rather_than_assumes_liquid():
    """None no debe interpretarse como 'sin problema' — la ausencia de dato
    es en sí misma un motivo de cautela, no se asume liquidez."""
    decision = decide_for_strategy(_clean_inputs(high_low_range_pct=None), "BALANCED")
    assert decision.trade_decision == "NO_TRADE"


# --- Casos combinados ---


def test_strategies_can_diverge_on_the_same_event():
    """El mismo evento puede ser TRADE para Aggressive y NO_TRADE para
    Conservative — es el comportamiento correcto, no un bug."""
    inputs = _clean_inputs(
        ev_by_strategy={
            "CONSERVATIVE": EV_THRESHOLDS["CONSERVATIVE"] + 0.0010,  # no supera el buffer
            "BALANCED": EV_THRESHOLDS["BALANCED"] + 0.0010,
            "AGGRESSIVE": EV_THRESHOLDS["AGGRESSIVE"] + 0.0200,  # sí supera el buffer con margen
        }
    )
    decisions = decide_all_strategies(inputs)
    assert decisions["CONSERVATIVE"].trade_decision == "NO_TRADE"
    assert decisions["AGGRESSIVE"].trade_decision in ("LONG", "SHORT")


def test_short_direction_when_net_conviction_negative():
    decision = decide_for_strategy(_clean_inputs(net_conviction=-0.6), "BALANCED")
    assert decision.trade_decision == "SHORT"


@pytest.mark.parametrize("strategy", ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"])
def test_all_three_strategies_are_covered_by_decide_all_strategies(strategy):
    decisions = decide_all_strategies(_clean_inputs())
    assert strategy in decisions
