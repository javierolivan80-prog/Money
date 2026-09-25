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

from pipeline.tests.fake_batch_api import validar_requests_como_la_api

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


class _ScriptedBatchesClient:
    """Responde con JSON válido para cualquier custom_id de bull/bear/judge,
    inspeccionando la request real en vez de una lista fija — así sirve para
    cualquier conjunto de eventos que le pase process_chunk()."""

    def __init__(self):
        self.call_count = 0

    def create(self, requests):
        validar_requests_como_la_api(requests)
        self.call_count += 1
        self._last_requests = requests
        return SimpleNamespace(id=f"batch_{self.call_count}", processing_status="ended")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        out = []
        for req in self._last_requests:
            custom_id = req["custom_id"]
            if custom_id.endswith("_bull"):
                payload = {"thesis": "bull thesis", "upside_drivers": ["d1"], "addressable_market": "big TAM", "comparable_events": "similar to X", "catalysts_forward": ["c1"]}
            elif custom_id.endswith("_bear"):
                payload = {"counter_thesis": "bear thesis", "downside_risks": ["r1"], "valuation_concern": "priced in", "historical_precedent": "failed at Y", "negative_catalysts": ["n1"]}
            elif custom_id.endswith("_judge"):
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


class _ClientQueMataLaConexion:
    """Imita lo que le pasó a producción (run 34964242549): mientras
    run_batch_and_collect espera a la Batch API, la conexión a Postgres muere
    de verdad (Neon corta las conexiones ociosas; la espera real duró unos 20
    minutos). El primer retrieve() mata la conexión Y devuelve "ended" en la
    misma llamada, así el test no necesita dormir de verdad."""

    def __init__(self, conexion_a_matar):
        self._conn = conexion_a_matar
        self._matada = False
        self._ultima_tanda = None

    def create(self, requests):
        validar_requests_como_la_api(requests)
        self._ultima_tanda = requests
        return SimpleNamespace(id="batch_mortal", processing_status="in_progress")

    def retrieve(self, batch_id):
        if not self._matada:
            self._conn.close()
            self._matada = True
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        out = []
        for req in self._ultima_tanda:
            custom_id = req["custom_id"]
            if custom_id.endswith("_bull"):
                payload = {"thesis": "bull thesis", "upside_drivers": ["d1"], "addressable_market": "big TAM", "comparable_events": "similar to X", "catalysts_forward": ["c1"]}
            elif custom_id.endswith("_bear"):
                payload = {"counter_thesis": "bear thesis", "downside_risks": ["r1"], "valuation_concern": "priced in", "historical_precedent": "failed at Y", "negative_catalysts": ["n1"]}
            elif custom_id.endswith("_judge"):
                payload = {"net_conviction": 0.6, "confidence_in_conviction": 80, "key_uncertainty": "u", "overriding_concern": "c"}
            else:
                continue
            content_block = SimpleNamespace(type="text", text=json.dumps(payload))
            message = SimpleNamespace(content=[content_block])
            result = SimpleNamespace(type="succeeded", message=message)
            out.append(SimpleNamespace(custom_id=custom_id, result=result))
        return out


def test_process_chunk_reconecta_si_la_conexion_muere_durante_la_espera_del_batch(conn):
    """EL bug real: 20 minutos esperando la Batch API dejaban la conexión
    ociosa hasta que Postgres la cortaba. El síntoma no salía en el batch —
    salía en el primer INSERT de después, con "the connection is lost", y el
    propio rollback de recuperación reventaba igual, tirando el chunk entero
    pese a que la IA ya había respondido. process_chunk tiene que detectarlo
    y reconectar ANTES de escribir, no después de que el primer INSERT
    reviente."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ClientQueMataLaConexion(conn)))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn_nueva = process_chunk(conn, client, events)

    assert conn_nueva is not conn  # se reconectó, no siguió con la muerta
    assert conn.closed  # la vieja, la que mató el fake client, sigue cerrada

    with conn_nueva.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        rows = cur.fetchall()
    assert len(rows) == 1  # el análisis se guardó pese a la reconexión
    conn_nueva.close()




def test_process_chunk_end_to_end_writes_full_event_analyses_row(conn):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    client = SimpleNamespace(messages=SimpleNamespace(batches=_ScriptedBatchesClient()))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)

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
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted_client.call_count == 2  # una llamada para bull/bear, otra para judge

    # Segundo evento: mismo ticker, misma clase, al día hábil siguiente (mismo
    # episodio, p. ej. una corrección) -> debe reusar Bull/Bear/Judge de caché
    # y NO generar nuevas llamadas al cliente.
    _seed_event(conn, "1", "TESTCO", dates[281].date())
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

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
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    _seed_event(conn, "1", "TESTCO", dates[290].date())
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))
    assert scripted_client.call_count == 4


def test_process_chunk_skips_llm_for_low_novelty_event_but_still_forces_no_trade(conn):
    """Optimización de coste (pedida explícitamente para gastar menos en la
    API de Anthropic): abstention_engine.NOVELTY_FLOOR hace NO_TRADE en las 3
    estrategias para cualquier evento con novelty_score por debajo del
    umbral, SIN mirar lo que diga Bull/Bear/Judge — es la PRIMERA de las 7
    condiciones que evalúa decide_for_strategy (ver su docstring). Pagar el
    debate de IA en ese caso no cambia ni una sola decisión, así que
    process_chunk debe descartarlo ANTES de construir el batch, no después.

    Se fuerza aquí un pre_event_drift_pct enorme (el precio de TESTCO ya subió
    un 30% justo antes del evento, así que el mercado lo tenía completamente
    descontado) — eso hunde novelty.score muy por debajo de NOVELTY_FLOOR=20
    (ver DRIFT_SATURATION_PCT=8.0 en novelty.py). Se verifica que el cliente
    de IA scripted nunca recibe una sola request Y que el veredicto final es
    idéntico al que habría dado el mismo camino con Bull/Bear/Judge reales."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    d0 = dates[280].date()
    _seed_event(conn, "1", "TESTCO", d0)

    # dates[280] (d0) es 2022-01-31 (lunes): D-5 calendario cae en 2022-01-26
    # (miércoles, índice 277 — un business day en sí mismo, no un fin de
    # semana) y D-1 calendario cae en el domingo 2022-01-30, cuyo
    # nearest_at_or_before es el viernes 2022-01-28 (índice 279). Por eso el
    # corte va en 278: todo lo anterior (incluido 277 = D-5) se deja en 50,
    # y desde 278 (que cubre 279 = D-1) se sube a 65 — verificado con un
    # cálculo directo de fetch_and_compute_enrichment antes de escribir esto,
    # no a ojo (un desajuste de un índice aquí deja el drift en 0%, no en 30%).
    with conn.cursor() as cur:
        for i in range(260, 278):
            cur.execute("UPDATE prices SET close_raw=50.0, high_raw=50.5, low_raw=49.5 WHERE ticker='TESTCO' AND trade_date=%s", (dates[i].date(),))
        for i in range(278, 281):
            cur.execute("UPDATE prices SET close_raw=65.0, high_raw=65.5, low_raw=64.5 WHERE ticker='TESTCO' AND trade_date=%s", (dates[i].date(),))
    conn.commit()

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    events = fetch_events_needing_analysis(conn)
    assert len(events) == 1

    conn = process_chunk(conn, client, events)

    assert scripted_client.call_count == 0  # cero llamadas a la Batch API: cero tokens gastados

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM event_analyses")
        row = cur.fetchone()
    assert float(row["novelty_score"]) < 20
    assert row["model_version_bull_bear"] == "SKIPPED_LOW_NOVELTY"
    assert row["model_version_judge"] == "SKIPPED_LOW_NOVELTY"
    assert row["trade_decision_conservative"] == "NO_TRADE"
    assert row["trade_decision_aggressive"] == "NO_TRADE"
    assert row["trade_decision_balanced"] == "NO_TRADE"
    bull_output = row["bull_analyst_output"] if isinstance(row["bull_analyst_output"], dict) else json.loads(row["bull_analyst_output"])
    assert bull_output["skipped_low_novelty"] is True


def test_process_chunk_high_novelty_event_still_calls_llm_as_before(conn):
    """Contraprueba de la anterior: un evento con novelty normal (el drift
    plano que ya seedeaba _seed_market_data, sin el salto de precio forzado)
    tiene que seguir llamando a Bull/Bear/Judge exactamente igual que antes
    del pre-filtro — el ahorro es solo para los eventos que iban a ser
    NO_TRADE de todas formas, nunca para los demás."""
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, process_chunk

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    scripted_client = _ScriptedBatchesClient()
    client = SimpleNamespace(messages=SimpleNamespace(batches=scripted_client))
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

    assert scripted_client.call_count == 2  # bull/bear + judge, como siempre
    with conn.cursor() as cur:
        cur.execute("SELECT model_version_bull_bear FROM event_analyses")
        row = cur.fetchone()
    assert row["model_version_bull_bear"] == "claude-haiku-4-5"


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
    conn = process_chunk(conn, client, fetch_events_needing_analysis(conn))

    with conn.cursor() as cur:
        cur.execute("SELECT novelty_reasoning FROM event_analyses ea JOIN events e ON e.event_id=ea.event_id WHERE e.d0_close_date = %s", (dates[280].date(),))
        row = cur.fetchone()
    reasoning = row["novelty_reasoning"] if isinstance(row["novelty_reasoning"], dict) else json.loads(row["novelty_reasoning"])
    assert reasoning["has_prior_guidance"] is True


def test_judge_fuera_de_rango_no_se_guarda(conn):
    """P0-2: sin minimum/maximum en el esquema, el rango se valida en Python.
    Un Judge con net_conviction=3 no debe llegar a event_analyses (ni recortado
    a 1: sería la convicción máxima inventada)."""
    from pipeline.analyze import event_analysis_pipeline as eap

    dates = _seed_market_data(conn, [("TESTCO", 50.0), ("SPY", 400.0), ("XLV", 100.0), ("^VIX", 18.0)])
    _seed_event(conn, "1", "TESTCO", dates[280].date())

    class _JudgeDesbocado(_ScriptedBatchesClient):
        def results(self, batch_id):
            out = super().results(batch_id)
            for r in out:
                if r.custom_id.endswith("_judge"):
                    r.result.message.content[0].text = json.dumps(
                        {"net_conviction": 3, "confidence_in_conviction": 80, "key_uncertainty": "u", "overriding_concern": "c"}
                    )
            return out

    client = SimpleNamespace(messages=SimpleNamespace(batches=_JudgeDesbocado()))
    conn = eap.process_chunk(conn, client, eap.fetch_events_needing_analysis(conn))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM event_analyses")
        assert cur.fetchone()["n"] == 0
