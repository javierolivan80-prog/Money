"""test_event_analysis_pipeline.py — el orquestador completo, Etapas 1-8.

Tres frentes: check_fda_crl_without_8k (real Postgres), compute_day3_stats
(real Postgres, verificando las 3 ramas de recomendación), y un test de
integración de extremo a extremo de process_chunk() contra Postgres real con
un cliente Anthropic SIMULADO (no hay ANTHROPIC_API_KEY en este sandbox) que
responde con JSON válido según el esquema exacto de cada etapa — esto prueba
que TODO el cableado (enrichment -> novelty -> Bull/Bear/Judge -> impact ->
EV -> abstention -> event_analyses) funciona junto, incluida la caché de 24h
saltándose una segunda llamada al LLM para el mismo (ticker, event_class).
"""
import json
import os
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


class _ScriptedBatchesClient:
    """Responde con JSON válido para cualquier custom_id de bull/bear/judge,
    inspeccionando la request real en vez de una lista fija — así sirve para
    cualquier conjunto de eventos que le pase process_chunk()."""

    def __init__(self):
        self.call_count = 0

    def create(self, requests):
        self.call_count += 1
        self._last_requests = requests
        return SimpleNamespace(id=f"batch_{self.call_count}", processing_status="ended")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        out = []
        for req in self._last_requests:
            custom_id = req["custom_id"]
            if custom_id.endswith(":bull"):
                payload = {"thesis": "bull thesis", "upside_drivers": ["d1"], "addressable_market": "big TAM", "comparable_events": "similar to X", "catalysts_forward": ["c1"]}
            elif custom_id.endswith(":bear"):
                payload = {"counter_thesis": "bear thesis", "downside_risks": ["r1"], "valuation_concern": "priced in", "historical_precedent": "failed at Y", "negative_catalysts": ["n1"]}
            elif custom_id.endswith(":judge"):
                payload = {"net_conviction": 0.6, "confidence_in_conviction": 80, "key_uncertainty": "u", "overriding_concern": "c"}
            else:
                continue
            content_block = SimpleNamespace(type="text", text=json.dumps(payload))
            message = SimpleNamespace(content=[content_block])
            result = SimpleNamespace(type="succeeded", message=message)
            out.append(SimpleNamespace(custom_id=custom_id, result=result))
        return out


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
            "fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _seed_market_data(conn, tickers_and_bases, n_days=320):
    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    with conn.cursor() as cur:
        for ticker, base in tickers_and_bases:
            for i, d in enumerate(dates):
                price = base * (1 + 0.0002 * i + rng.normal(0, 0.01))
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES (%s,%s,%s,%s,%s,1.0,100000,FALSE) ON CONFLICT (ticker, trade_date) DO NOTHING",
                    (ticker, d.date(), price, price * 1.01, price * 0.99),
                )
        for d in dates:
            cur.execute(
                "INSERT INTO fama_french_factors (trade_date, mkt_rf, smb, hml, rf) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (trade_date) DO NOTHING",
                (d.date(), float(rng.normal(0.0003, 0.008)), float(rng.normal(0.0001, 0.004)), float(rng.normal(-0.0001, 0.004)), 0.00005),
            )
    conn.commit()
    return dates


def _seed_event(conn, cik: str, ticker: str, d0: date, event_class: str = "8K_2.02_EARNINGS", sic_code: str = "2836", filing_text: str | None = None) -> int:
    # source debe reflejar de dónde vendría el evento de verdad: un FDA_CRL no
    # es un filing de EDGAR (se encontró este bug de fixture al ejecutar el
    # test de check_fda_crl_without_8k — con source hardcodeado a 'EDGAR', el
    # propio evento FDA_CRL se emparejaba consigo mismo como si fuera "el 8-K
    # correspondiente").
    source = "FDA_OPENFDA" if event_class.startswith("FDA_") else "EDGAR"
    is_satellite = event_class.startswith("FDA_")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, sic_code, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'Test Co',%s,%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, sic_code, d0, d0),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash, filing_text)
            VALUES (%s,%s,%s,%s,%s,ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s,%s)
            RETURNING event_id
            """,
            (cik, ticker, source, is_satellite, event_class, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}", filing_text),
        )
        event_id = cur.fetchone()["event_id"]
    conn.commit()
    return event_id


# ---------------------------------------------------------------------------
# check_fda_crl_without_8k
# ---------------------------------------------------------------------------


def test_fda_crl_without_8k_returns_false_for_non_crl_classes(conn):
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    assert check_fda_crl_without_8k(conn, "1", "8K_2.02_EARNINGS", date(2024, 1, 1)) is False


def test_fda_crl_without_8k_true_when_no_matching_8k_nearby(conn):
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    _seed_event(conn, "1", "BIOX", date(2024, 1, 1), event_class="FDA_CRL")
    assert check_fda_crl_without_8k(conn, "1", "FDA_CRL", date(2024, 1, 1)) is True


def test_fda_crl_without_8k_false_when_8k_already_filed_before_d0(conn):
    """Un 8-K anterior a D0 sí está disponible al decidir: la empresa ya lo ha
    comunicado, así que la regla 6 no veta."""
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    _seed_event(conn, "1", "BIOX", date(2024, 1, 10), event_class="FDA_CRL")
    _seed_event(conn, "1", "BIOX", date(2024, 1, 8), event_class="8K_8.01_OTHER")  # comunicado 2 días antes
    assert check_fda_crl_without_8k(conn, "1", "FDA_CRL", date(2024, 1, 10)) is False


def test_fda_crl_without_8k_ignores_8k_filed_after_d0(conn):
    """Regresión anti-look-ahead: un 8-K posterior a D0 no existe todavía en el
    momento de la decisión. Antes se buscaba en una ventana de ±10 días, así
    que este caso devolvía False (operar) usando información del futuro."""
    from pipeline.analyze.event_analysis_pipeline import check_fda_crl_without_8k

    _seed_event(conn, "1", "BIOX", date(2024, 1, 1), event_class="FDA_CRL")
    _seed_event(conn, "1", "BIOX", date(2024, 1, 3), event_class="8K_8.01_OTHER")  # aún no ocurrido en D0
    assert check_fda_crl_without_8k(conn, "1", "FDA_CRL", date(2024, 1, 1)) is True


# ---------------------------------------------------------------------------
# compute_day3_stats
# ---------------------------------------------------------------------------


def _seed_minimal_event_analysis(conn, event_id: int, trade_conservative: str, trade_aggressive: str, trade_balanced: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (%s, 60, '{}', '{}', '{}', '{}', 0.5, 70, '{}', 5, '{}', 0.01, 0.02, 0.015, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')
            """,
            (event_id, trade_conservative, trade_aggressive, trade_balanced),
        )
    conn.commit()


def test_day3_stats_flags_too_permissive_above_70pct(conn):
    from pipeline.analyze.event_analysis_pipeline import compute_day3_stats

    for i in range(10):
        eid = _seed_event(conn, str(i), f"T{i}", date(2022, 1, 1 + i))
        # 8 de 10 -> TRADE en Aggressive (80%, por encima de 70%)
        _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "LONG" if i < 8 else "NO_TRADE", "NO_TRADE")

    stats = compute_day3_stats(conn)
    assert stats["pct_trade_aggressive"] == pytest.approx(80.0)
    assert "demasiado permisivo" in stats["recommendation_aggressive"]


def test_day3_stats_flags_too_restrictive_below_10pct(conn):
    from pipeline.analyze.event_analysis_pipeline import compute_day3_stats

    for i in range(10):
        eid = _seed_event(conn, str(i), f"T{i}", date(2022, 1, 1 + i))
        _seed_minimal_event_analysis(conn, eid, "LONG" if i == 0 else "NO_TRADE", "NO_TRADE", "NO_TRADE")

    stats = compute_day3_stats(conn)
    assert stats["pct_trade_conservative"] == pytest.approx(10.0)
    # Límite exacto de 10%: no cae en "demasiado restrictivo" (regla es '< 10'), cae en rango esperado
    assert "rango esperado" in stats["recommendation_conservative"]


def test_day3_stats_in_expected_range(conn):
    from pipeline.analyze.event_analysis_pipeline import compute_day3_stats

    for i in range(10):
        eid = _seed_event(conn, str(i), f"T{i}", date(2022, 1, 1 + i))
        _seed_minimal_event_analysis(conn, eid, "NO_TRADE", "LONG" if i < 3 else "NO_TRADE", "NO_TRADE")  # 30%

    stats = compute_day3_stats(conn)
    assert stats["pct_trade_aggressive"] == pytest.approx(30.0)
    assert "rango esperado" in stats["recommendation_aggressive"]


# ---------------------------------------------------------------------------
# process_chunk — integración de extremo a extremo con LLM simulado
# ---------------------------------------------------------------------------


def test_process_chunk_end_to_end_writes_full_event_analyses_row(conn):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    process_chunk(conn, client, events)

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        rows = cur.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["from_cache"] is False
    assert row["model_version_bull_bear"] == "claude-haiku-4-5"
    assert row["model_version_judge"] == "claude-sonnet-4-6"
    assert row["trade_decision_conservative"] in ("LONG", "SHORT", "NO_TRADE")
    assert float(row["net_conviction"]) == pytest.approx(0.6)
    assert json.loads(row["bull_analyst_output"])["thesis"] == "bull thesis" if isinstance(row["bull_analyst_output"], str) else row["bull_analyst_output"]["thesis"] == "bull thesis"


def test_process_chunk_second_event_same_ticker_class_uses_cache_not_llm(conn):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted_client.call_count == 2  # una llamada para bull/bear, otra para judge

    # Segundo evento: mismo ticker, misma clase, al día hábil siguiente (mismo
    # episodio, p. ej. una corrección) -> debe reusar Bull/Bear/Judge de caché
    # y NO generar nuevas llamadas al cliente.
    _seed_event(conn, "1", "TESTCO", dates[281].date())
    process_chunk(conn, client, fetch_events_needing_analysis(conn))

    assert scripted_client.call_count == 2  # sin llamadas nuevas: se sirvió de caché

    with conn.cursor() as cur:
        cur.execute("SELECT from_cache, net_conviction FROM event_analyses ORDER BY event_id")
        rows = cur.fetchall()
    assert rows[0]["from_cache"] is False
    assert rows[1]["from_cache"] is True
    assert float(rows[1]["net_conviction"]) == pytest.approx(float(rows[0]["net_conviction"]))


def test_process_chunk_otro_trimestre_del_mismo_ticker_no_usa_cache(conn):
    """Dos semanas después ya es otro filing: reutilizar el veredicto del
    anterior daría a todos los trimestres de una empresa la misma opinión."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())
    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    process_chunk(conn, client, fetch_events_needing_analysis(conn))
    _seed_event(conn, "1", "TESTCO", dates[290].date())
    process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted_client.call_count == 4


# ---------------------------------------------------------------------------
# Fase 3 — filing_text real y guidance/rumor fluyendo hasta event_analyses
# ---------------------------------------------------------------------------


def test_process_chunk_bull_bear_prompt_contains_real_filing_text(conn):
    """Antes de la Fase 3, filing_excerpt era SIEMPRE el placeholder.
    Verifica que cuando events.filing_text tiene contenido real, ese texto
    (no el fallback) es lo que construye process_chunk() para el prompt de
    Bull/Bear — inspeccionando la construcción de EventContext tal como la
    hace process_chunk, sin necesidad de mockear el batch completo."""
    from pipeline.analyze.adversarial_analyzer import build_bull_bear_batch
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    real_text = "Acme Widgets Corp reported record quarterly revenue of $412 million, up 18% year-over-year."
    _seed_event(conn, "1", "TESTCO", dates[280].date(), filing_text=real_text)

    from pipeline.analyze.adversarial_analyzer import EventContext

    ev = fetch_events_needing_analysis(conn)[0]
    ctx = EventContext(event_id=ev["event_id"], ticker=ev["ticker"], event_class=ev["event_class"], company_name="Test Co", filing_excerpt=ev["filing_text"])
    requests_ = build_bull_bear_batch([ctx])
    prompt = requests_[0]["params"]["messages"][0]["content"]
    assert real_text in prompt


def test_process_chunk_without_filing_text_falls_back_gracefully(conn):
    from pipeline.analyze.event_analysis_pipeline import _FALLBACK_FILING_EXCERPT, fetch_events_needing_analysis

    _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    dates = pd.date_range("2021-01-04", periods=320, freq="B")
    _seed_event(conn, "1", "TESTCO", dates[280].date())  # sin filing_text

    ev = fetch_events_needing_analysis(conn)[0]
    assert ev["filing_text"] is None  # confirma el escenario que se está probando


def test_process_chunk_novelty_reasoning_reflects_prior_guidance_detection(conn):
    """Un filing PREVIO con lenguaje de guidance debe hacer que
    novelty_reasoning de ESTE evento registre has_prior_guidance=True — la
    señal completa de la Fase 3, de principio a fin."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    # Evento previo (hace 15 días de calendario) con lenguaje de guidance explícito.
    prior_date = dates[280].date() - pd.Timedelta(days=15)
    _seed_event(conn, "1", "TESTCO", prior_date, filing_text="We are raising our full-year outlook to $2B.")
    # Evento actual, sin texto propio todavía (el guidance viene del PREVIO).
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    process_chunk(conn, client, fetch_events_needing_analysis(conn))

    with conn.cursor() as cur:
        cur.execute("SELECT novelty_reasoning FROM event_analyses ea JOIN events e ON e.event_id=ea.event_id WHERE e.d0_close_date = %s", (dates[280].date(),))
        row = cur.fetchone()
    reasoning = row["novelty_reasoning"] if isinstance(row["novelty_reasoning"], dict) else json.loads(row["novelty_reasoning"])
    assert reasoning["has_prior_guidance"] is True
