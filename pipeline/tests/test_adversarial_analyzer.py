"""test_adversarial_analyzer.py — prueba lo que NO requiere llamar a la API real.

No hay ANTHROPIC_API_KEY en este sandbox (verificado), así que ningún test
aquí hace una llamada de red real. Se prueban: el cálculo de ev_score (lógica
pura), la construcción de requests de batch (forma del payload), y el parseo
de resultados usando objetos mock con la MISMA forma que devuelve el SDK real
(result.result.type, result.result.message.content[].text) según
batches.md del skill claude-api.
"""
import json
from types import SimpleNamespace

from pipeline.analyze.adversarial_analyzer import (
    EventContext,
    build_bull_bear_batch,
    build_judge_batch,
    compute_ev_score,
    run_batch_and_collect,
)


def test_compute_ev_score_is_signed_product():
    assert compute_ev_score(5.0, 0.8) == 4.0
    assert compute_ev_score(-3.0, 0.5) == -1.5
    assert compute_ev_score(0.0, 0.9) == 0.0


def _sample_event():
    return EventContext(
        event_id=42,
        ticker="ACME",
        event_class="8K_2.02_EARNINGS",
        company_name="Acme Widgets Corp",
        filing_excerpt="Q3 revenue beat by 8%, guidance raised.",
    )


def test_build_bull_bear_batch_produces_two_requests_per_event_with_distinct_custom_ids():
    requests_ = build_bull_bear_batch([_sample_event()])
    custom_ids = {r["custom_id"] for r in requests_}
    assert custom_ids == {"42:bull", "42:bear"}


def test_build_bull_bear_batch_uses_batch_model_and_json_schema_format():
    requests_ = build_bull_bear_batch([_sample_event()])
    for r in requests_:
        assert r["params"]["model"] == "claude-haiku-4-5"
        assert r["params"]["output_config"]["format"]["type"] == "json_schema"
        assert "expected_move_pct" in r["params"]["output_config"]["format"]["schema"]["properties"]


def test_build_judge_batch_skips_events_missing_bull_or_bear():
    events = [_sample_event()]
    incomplete_results = {"42:bull": {"thesis": "x", "expected_move_pct": 1.0, "confidence": 0.5}}
    requests_ = build_judge_batch(events, incomplete_results)
    assert requests_ == []  # sin bear, no se puede construir el prompt del judge


def test_build_judge_batch_embeds_both_theses_in_prompt():
    events = [_sample_event()]
    results = {
        "42:bull": {"thesis": "Guidance raised, momentum strong", "expected_move_pct": 6.0, "confidence": 0.7},
        "42:bear": {"thesis": "Beat was low quality, one-off tax benefit", "expected_move_pct": -2.0, "confidence": 0.4},
    }
    requests_ = build_judge_batch(events, results)
    assert len(requests_) == 1
    prompt_text = requests_[0]["params"]["messages"][0]["content"]
    assert "Guidance raised" in prompt_text
    assert "one-off tax benefit" in prompt_text


def _mock_batch_result(custom_id: str, result_type: str, json_payload: dict | None = None):
    """Construye un objeto con la misma forma que result.result de la Batch
    API real (ver batches.md: result.result.type, result.result.message.content)."""
    if result_type == "succeeded":
        content_block = SimpleNamespace(type="text", text=json.dumps(json_payload))
        message = SimpleNamespace(content=[content_block])
        result = SimpleNamespace(type="succeeded", message=message)
    else:
        result = SimpleNamespace(type=result_type)
    return SimpleNamespace(custom_id=custom_id, result=result)


class _FakeBatchesClient:
    """Mock mínimo de client.messages.batches para probar run_batch_and_collect
    sin red. Simula 1 request exitosa, 1 errored y 1 con JSON corrupto."""

    def __init__(self):
        self._batch_state = SimpleNamespace(id="batch_test123", processing_status="ended")

    def create(self, requests):
        return self._batch_state

    def retrieve(self, batch_id):
        return self._batch_state

    def results(self, batch_id):
        return [
            _mock_batch_result("1:bull", "succeeded", {"thesis": "ok", "expected_move_pct": 3.0, "confidence": 0.6}),
            _mock_batch_result("2:bull", "errored"),
            _mock_batch_result("3:bull", "succeeded", None),  # se sobreescribe abajo con JSON corrupto
        ]


def test_run_batch_and_collect_skips_errored_and_malformed_results():
    client = SimpleNamespace(messages=SimpleNamespace(batches=_FakeBatchesClient()))

    # Sobreescribe el tercer resultado para simular JSON corrupto (rama de
    # json.JSONDecodeError en run_batch_and_collect).
    original_results = client.messages.batches.results

    def patched_results(batch_id):
        rows = list(original_results(batch_id))
        rows[2] = _mock_batch_result("3:bull", "succeeded")
        rows[2].result.message.content[0].text = "{not valid json"
        return rows

    client.messages.batches.results = patched_results

    results, batch_id = run_batch_and_collect(client, requests_=[])
    assert batch_id == "batch_test123"
    assert set(results.keys()) == {"1:bull"}  # solo la exitosa y bien formada sobrevive
    assert results["1:bull"]["expected_move_pct"] == 3.0
