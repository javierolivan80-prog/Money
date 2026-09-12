"""test_adversarial_analyzer.py — Etapas 3-5, reescrito para los esquemas de
Fase 2. No hay ANTHROPIC_API_KEY en este sandbox (verificado), así que ningún
test aquí hace una llamada de red real. Se prueban: construcción de requests
de batch con los esquemas y modelos correctos (Haiku para Bull/Bear, Sonnet
4.6 para Judge), parseo de resultados con objetos mock con la forma exacta
del SDK, y la caché de 24h contra Postgres real.
"""
import json
import os
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from pipeline.analyze.adversarial_analyzer import (
    EventContext,
    build_bull_bear_batch,
    build_judge_batch,
    get_cached_analysis,
    run_batch_and_collect,
)


def _sample_event(event_id=42):
    return EventContext(
        event_id=event_id,
        ticker="ACME",
        event_class="8K_2.02_EARNINGS",
        company_name="Acme Widgets Corp",
        filing_excerpt="Q3 revenue beat by 8%, guidance raised.",
    )


# ---------------------------------------------------------------------------
# Construcción de batches
# ---------------------------------------------------------------------------


def test_build_bull_bear_batch_produces_two_requests_with_distinct_custom_ids_and_schemas():
    requests_ = build_bull_bear_batch([_sample_event()])
    by_id = {r["custom_id"]: r for r in requests_}
    assert set(by_id.keys()) == {"42:bull", "42:bear"}

    bull_props = by_id["42:bull"]["params"]["output_config"]["format"]["schema"]["properties"]
    assert set(bull_props.keys()) == {"thesis", "upside_drivers", "addressable_market", "comparable_events", "catalysts_forward"}

    bear_props = by_id["42:bear"]["params"]["output_config"]["format"]["schema"]["properties"]
    assert set(bear_props.keys()) == {"counter_thesis", "downside_risks", "valuation_concern", "historical_precedent", "negative_catalysts"}


def test_build_bull_bear_batch_uses_haiku_for_both_sides():
    for r in build_bull_bear_batch([_sample_event()]):
        assert r["params"]["model"] == "claude-haiku-4-5"


def test_build_judge_batch_uses_sonnet_4_6_explicitly():
    events = [_sample_event()]
    results = {
        "42:bull": {"thesis": "x", "upside_drivers": ["a"], "addressable_market": "big", "comparable_events": "y", "catalysts_forward": ["c"]},
        "42:bear": {"counter_thesis": "z", "downside_risks": ["r"], "valuation_concern": "v", "historical_precedent": "h", "negative_catalysts": ["n"]},
    }
    requests_ = build_judge_batch(events, results)
    assert len(requests_) == 1
    assert requests_[0]["params"]["model"] == "claude-sonnet-4-6"
    judge_props = requests_[0]["params"]["output_config"]["format"]["schema"]["properties"]
    assert set(judge_props.keys()) == {"net_conviction", "confidence_in_conviction", "key_uncertainty", "overriding_concern"}


def test_build_judge_batch_skips_events_missing_bull_or_bear():
    events = [_sample_event()]
    incomplete = {"42:bull": {"thesis": "x", "upside_drivers": [], "addressable_market": "", "comparable_events": "", "catalysts_forward": []}}
    assert build_judge_batch(events, incomplete) == []


def test_build_judge_batch_embeds_both_analyses_in_prompt():
    events = [_sample_event()]
    results = {
        "42:bull": {"thesis": "Guidance raised, momentum strong", "upside_drivers": ["driver1"], "addressable_market": "big TAM", "comparable_events": "similar to X", "catalysts_forward": ["cat1"]},
        "42:bear": {"counter_thesis": "Beat was low quality", "downside_risks": ["risk1"], "valuation_concern": "already priced in", "historical_precedent": "failed before at Y", "negative_catalysts": ["neg1"]},
    }
    requests_ = build_judge_batch(events, results)
    prompt = requests_[0]["params"]["messages"][0]["content"]
    assert "Guidance raised" in prompt
    assert "Beat was low quality" in prompt
    assert "similar to X" in prompt
    assert "failed before at Y" in prompt


# ---------------------------------------------------------------------------
# run_batch_and_collect — con mocks del SDK
# ---------------------------------------------------------------------------


def _mock_batch_result(custom_id: str, result_type: str, json_payload: dict | None = None):
    if result_type == "succeeded":
        content_block = SimpleNamespace(type="text", text=json.dumps(json_payload))
        message = SimpleNamespace(content=[content_block])
        result = SimpleNamespace(type="succeeded", message=message)
    else:
        result = SimpleNamespace(type=result_type)
    return SimpleNamespace(custom_id=custom_id, result=result)


class _FakeBatchesClient:
    def __init__(self):
        self._batch_state = SimpleNamespace(id="batch_test123", processing_status="ended")

    def create(self, requests):
        return self._batch_state

    def retrieve(self, batch_id):
        return self._batch_state

    def results(self, batch_id):
        return [
            _mock_batch_result("1:judge", "succeeded", {"net_conviction": 0.6, "confidence_in_conviction": 75, "key_uncertainty": "u", "overriding_concern": "c"}),
            _mock_batch_result("2:judge", "errored"),
        ]


def test_run_batch_and_collect_skips_errored_results():
    client = SimpleNamespace(messages=SimpleNamespace(batches=_FakeBatchesClient()))
    results, batch_id = run_batch_and_collect(client, requests_=[])
    assert batch_id == "batch_test123"
    assert set(results.keys()) == {"1:judge"}
    assert results["1:judge"]["net_conviction"] == 0.6


# ---------------------------------------------------------------------------
# Caché de 24h — contra Postgres real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
class TestCacheAgainstRealPostgres:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from pipeline.db.connection import get_connection, init_schema

        self.conn = get_connection()
        init_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(
                "TRUNCATE car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
                "fama_french_factors, universe RESTART IDENTITY CASCADE"
            )
        self.conn.commit()
        yield
        self.conn.close()

    def _insert_event_with_analysis(self, cik: str, ticker: str, event_class: str, analyzed_at_sql_interval: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s,%s,'X','2024-01-01','2024-01-01') ON CONFLICT (cik) DO NOTHING",
                (cik, ticker),
            )
            cur.execute(
                """
                INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['2.02'],%s,'https://x','2024-01-01','2024-01-01','RULE',1.0,%s)
                RETURNING event_id
                """,
                (cik, ticker, event_class, f"acc-{cik}", f"hash-{cik}"),
            )
            event_id = cur.fetchone()["event_id"]
            cur.execute(
                f"""
                INSERT INTO event_analyses (
                    event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                    judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                    n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                    abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                    trade_decision_balanced, model_version_bull_bear, model_version_judge, analyzed_at
                ) VALUES (
                    %s, 80, '{{}}', '{{}}', '{{}}', '{{}}', 0.5, 70, '{{}}', 10, '{{}}', 0.01, 0.02, 0.015,
                    '{{}}', 'LONG', 'LONG', 'LONG', 'claude-haiku-4-5', 'claude-sonnet-4-6',
                    now() - interval '{analyzed_at_sql_interval}'
                )
                """,
                (event_id,),
            )
        self.conn.commit()
        return event_id

    def test_cache_hit_within_24h_window(self):
        self._insert_event_with_analysis("1", "ACME", "8K_2.02_EARNINGS", "2 hours")
        cached = get_cached_analysis(self.conn, "ACME", "8K_2.02_EARNINGS", date.today())
        assert cached is not None
        assert cached["net_conviction"] == pytest.approx(0.5)

    def test_cache_miss_outside_24h_window(self):
        self._insert_event_with_analysis("2", "ACME", "8K_2.02_EARNINGS", "25 hours")
        cached = get_cached_analysis(self.conn, "ACME", "8K_2.02_EARNINGS", date.today())
        assert cached is None

    def test_cache_is_scoped_to_ticker_and_event_class(self):
        self._insert_event_with_analysis("3", "ACME", "8K_2.02_EARNINGS", "1 hour")
        # Mismo ticker, distinta clase de evento -> no debe dar cache hit.
        assert get_cached_analysis(self.conn, "ACME", "8K_1.01_MATERIAL_AGMT", date.today()) is None
        # Misma clase, distinto ticker -> tampoco.
        assert get_cached_analysis(self.conn, "OTHER", "8K_2.02_EARNINGS", date.today()) is None
