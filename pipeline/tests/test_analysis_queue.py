"""test_analysis_queue.py — la cola de la IA y la lista de empresas.

Cubre tres fallos reales encontrados en la auditoría de 2026-09-25:
  - run_pipeline() entraba en bucle infinito (re-pagando el batch) si un
    evento volvía de la IA sin Bull/Bear/Judge completo;
  - universe.market_cap_last_usd / in_investable_universe no los calculaba
    nadie, así que no había forma de priorizar empresas grandes;
  - eventos sin texto de filing se mandaban a la IA con un placeholder.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from pipeline.ingest.universe_maintenance import is_investable

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


# --- is_investable (sin BD) ---

def test_is_investable_aplica_los_tres_umbrales():
    assert is_investable(50.0, 2e9, 5e6) is True
    assert is_investable(4.0, 2e9, 5e6) is False      # precio < 5 $
    assert is_investable(50.0, 1e8, 5e6) is False     # cap < 300 M$
    assert is_investable(50.0, 2e9, 5e5) is False     # volumen < 1 M$/día


def test_is_investable_dato_ausente_es_no_invertible():
    assert is_investable(None, 2e9, 5e6) is False
    assert is_investable(50.0, None, 5e6) is False
    assert is_investable(50.0, 2e9, None) is False


def test_billing_error_se_distingue_de_otros_400():
    import anthropic
    import httpx2 as httpx

    from pipeline.analyze.event_analysis_pipeline import _is_billing_error

    def _err(msg):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")
        resp = httpx.Response(400, request=req)
        return anthropic.BadRequestError(msg, response=resp, body=None)

    assert _is_billing_error(_err("Your credit balance is too low to access the Anthropic API.")) is True
    assert _is_billing_error(_err("max_tokens: field required")) is False
    assert _is_billing_error(RuntimeError("credit balance")) is False


# --- Con Postgres ---

@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE car_results, event_analyses, event_enrichment, events, prices, fundamentals, "
            "quality_scores, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _empresa(conn, cik, ticker, price, shares, volume=1_000_000, last_day=None):
    last_day = last_day or date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,%s,'2024-01-01','2024-01-01')",
            (cik, ticker, f"{ticker} Inc"),
        )
        for i in range(5):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume) VALUES (%s,%s,%s,1,%s)",
                (ticker, last_day - timedelta(days=i), price, volume),
            )
        if shares is not None:
            cur.execute(
                "INSERT INTO fundamentals (cik, fiscal_period_end, filed_at, form, shares_outstanding) "
                "VALUES (%s,'2023-12-31','2024-02-15','10-K',%s)",
                (cik, shares),
            )
    conn.commit()


def _evento(conn, cik, ticker, d0, text="texto real del filing"):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, event_class, accession_number, source_url, filed_at,
                d0_close_date, classification_method, raw_text_hash, filing_text)
            VALUES (%s,%s,'EDGAR','8K_2.02_EARNINGS',%s,'https://x',%s,%s,'RULE',%s,%s)
            RETURNING event_id
            """,
            (cik, ticker, f"acc-{cik}-{d0}", d0, d0, f"h-{cik}-{d0}", text),
        )
        eid = cur.fetchone()["event_id"]
    conn.commit()
    return eid


@pytestmark_db
def test_refresh_universe_metrics_calcula_cap_y_flag(conn):
    from pipeline.ingest.universe_maintenance import refresh_universe_metrics, top_companies

    _empresa(conn, "1", "BIG", 100.0, 1e9)            # 100 B$, 100 M$/día
    _empresa(conn, "2", "SMALL", 10.0, 1e7)           # 100 M$ -> fuera
    _empresa(conn, "3", "NOSHARES", 50.0, None)       # sin acciones -> fuera
    _empresa(conn, "4", "STALE", 100.0, 1e9, last_day=date.today() - timedelta(days=60))  # precio viejo

    resumen = refresh_universe_metrics(conn)
    assert resumen["invertibles"] == 1
    assert resumen["grandes_10b"] == 1

    with conn.cursor() as cur:
        cur.execute("SELECT ticker, market_cap_last_usd, adv_usd_60d, in_investable_universe FROM universe ORDER BY cik")
        rows = {r["ticker"]: r for r in cur.fetchall()}
    assert float(rows["BIG"]["market_cap_last_usd"]) == pytest.approx(100e9)
    assert float(rows["BIG"]["adv_usd_60d"]) == pytest.approx(100e6)
    assert rows["BIG"]["in_investable_universe"] is True
    assert rows["SMALL"]["in_investable_universe"] is False
    assert rows["NOSHARES"]["in_investable_universe"] is False
    assert rows["STALE"]["in_investable_universe"] is False

    lista = top_companies(conn, 10)
    assert [r["ticker"] for r in lista] == ["BIG"]


@pytestmark_db
def test_cola_prioriza_grandes_y_exige_texto(conn):
    from pipeline.analyze.event_analysis_pipeline import analysis_queue_summary, fetch_events_needing_analysis
    from pipeline.ingest.universe_maintenance import refresh_universe_metrics

    _empresa(conn, "1", "MEGA", 100.0, 3e9)    # 300 B$
    _empresa(conn, "2", "MID", 50.0, 1e8)      # 5 B$
    _empresa(conn, "3", "TINY", 10.0, 1e7)     # 100 M$ -> fuera del universo
    refresh_universe_metrics(conn)
    d = date(2026, 9, 1)
    e_mid = _evento(conn, "2", "MID", d)
    e_mega = _evento(conn, "1", "MEGA", d)
    _evento(conn, "3", "TINY", d)
    _evento(conn, "1", "MEGA", d - timedelta(days=1), text=None)  # sin texto todavía

    cola = fetch_events_needing_analysis(conn, 50, min_market_cap=300e6, require_text=True)
    assert [ev["event_id"] for ev in cola] == [e_mega, e_mid]

    solo_grandes = fetch_events_needing_analysis(conn, 50, min_market_cap=10e9, require_text=True)
    assert [ev["event_id"] for ev in solo_grandes] == [e_mega]

    resumen = analysis_queue_summary(conn, min_market_cap=300e6)
    assert resumen["pendientes"] == 4
    assert resumen["en_objetivo"] == 3
    assert resumen["listos"] == 2


class _ClienteQueFalla:
    """Batch que siempre vuelve 'errored': ningún evento llega a guardarse."""

    def __init__(self):
        self.creates = 0

    def create(self, requests):
        assert requests, "batch vacío: la API real lo rechaza con un 400"
        self.creates += 1
        self.sizes = getattr(self, "sizes", []) + [len(requests)]
        self._reqs = requests
        return SimpleNamespace(id=f"b{self.creates}", processing_status="ended")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id):
        return [SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="errored")) for r in self._reqs]


@pytestmark_db
def test_run_pipeline_no_entra_en_bucle_si_la_ia_falla(conn):
    from pipeline.analyze.event_analysis_pipeline import run_pipeline

    _empresa(conn, "1", "MEGA", 100.0, 3e9)
    _evento(conn, "1", "MEGA", date(2026, 9, 1))
    fake = _ClienteQueFalla()
    client = SimpleNamespace(messages=SimpleNamespace(batches=fake))

    # Antes: bucle infinito. Ahora: un intento por evento y por corrida.
    procesados, _ = run_pipeline(conn, client, max_chunks=10)
    assert procesados == 1
    assert fake.creates == 1  # un batch Bull/Bear; sin Bull/Bear no hay batch de Judge, y se acabó


@pytestmark_db
def test_run_pipeline_respeta_el_tope_de_eventos(conn):
    from pipeline.analyze.event_analysis_pipeline import fetch_events_needing_analysis, run_pipeline

    _empresa(conn, "1", "MEGA", 100.0, 3e9)
    for i in range(7):
        _evento(conn, "1", "MEGA", date(2026, 9, 1) + timedelta(days=i))
    fake = _ClienteQueFalla()
    client = SimpleNamespace(messages=SimpleNamespace(batches=fake))

    procesados, conn = run_pipeline(conn, client, max_events=3)
    assert procesados == 3
    assert fake.sizes == [6]  # 3 eventos x (bull + bear), y ni uno más
    assert len(fetch_events_needing_analysis(conn, 50)) == 7  # nada guardado: siguen en cola


@pytestmark_db
def test_price_tickers_incluye_referencias_aunque_el_universo_este_vacio(conn):
    from pipeline.ingest.universe_maintenance import price_tickers

    assert {"SPY", "^VIX", "XLK"} <= set(price_tickers(conn))
    _empresa(conn, "1", "MEGA", 100.0, 3e9)
    assert "MEGA" in price_tickers(conn)
