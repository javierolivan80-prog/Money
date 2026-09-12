"""test_novelty.py — Etapa 2, lógica pura."""
import pytest

from pipeline.analyze.novelty import DRIFT_SATURATION_PCT, NoveltyInputs, compute_novelty


def test_zero_drift_and_no_guidance_no_rumor_gives_max_novelty():
    result = compute_novelty(NoveltyInputs(pre_event_drift_pct=0.0, has_prior_guidance=False, rumor_flag=False))
    assert result.score == pytest.approx(100.0)


def test_large_drift_and_prior_guidance_and_rumors_gives_low_novelty():
    result = compute_novelty(
        NoveltyInputs(pre_event_drift_pct=DRIFT_SATURATION_PCT * 2, has_prior_guidance=True, rumor_flag=True)
    )
    assert result.score < 25.0


def test_drift_component_saturates_at_the_ceiling():
    at_ceiling = compute_novelty(NoveltyInputs(pre_event_drift_pct=DRIFT_SATURATION_PCT, has_prior_guidance=None, rumor_flag=None))
    beyond_ceiling = compute_novelty(NoveltyInputs(pre_event_drift_pct=DRIFT_SATURATION_PCT * 3, has_prior_guidance=None, rumor_flag=None))
    assert at_ceiling.score == pytest.approx(0.0, abs=0.01)
    assert beyond_ceiling.score == pytest.approx(0.0, abs=0.01)  # no se vuelve negativo


def test_drift_direction_does_not_matter_only_magnitude():
    positive = compute_novelty(NoveltyInputs(pre_event_drift_pct=5.0))
    negative = compute_novelty(NoveltyInputs(pre_event_drift_pct=-5.0))
    assert positive.score == pytest.approx(negative.score)


def test_missing_guidance_and_rumor_signals_are_reported_not_hidden():
    result = compute_novelty(NoveltyInputs(pre_event_drift_pct=2.0))
    assert "has_prior_guidance" in result.components_unavailable
    assert "rumor_flag" in result.components_unavailable
    assert result.components_used == ["drift"]
    assert result.reasoning["note_on_unavailable"] is not None


def test_score_with_only_drift_available_equals_the_drift_component_alone():
    """Cuando faltan guidance/rumor, el score debe ser EXACTAMENTE el
    componente de drift (tras renormalizar pesos a un único componente
    disponible) — no un promedio contaminado por asumir 'neutral' para lo
    que falta."""
    from pipeline.analyze.novelty import _drift_component

    drift_pct = 3.0
    result = compute_novelty(NoveltyInputs(pre_event_drift_pct=drift_pct))
    assert result.score == pytest.approx(_drift_component(drift_pct))


def test_all_three_components_present_changes_score_relative_to_drift_alone():
    drift_only = compute_novelty(NoveltyInputs(pre_event_drift_pct=1.0))
    with_guidance_present = compute_novelty(NoveltyInputs(pre_event_drift_pct=1.0, has_prior_guidance=True, rumor_flag=False))
    assert with_guidance_present.score != drift_only.score


def test_as_json_includes_score_and_component_lists():
    result = compute_novelty(NoveltyInputs(pre_event_drift_pct=1.0, has_prior_guidance=False, rumor_flag=None))
    payload = result.as_json()
    assert "score" in payload
    assert payload["components_used"] == ["drift", "guidance"]
    assert payload["components_unavailable"] == ["rumor_flag"]
