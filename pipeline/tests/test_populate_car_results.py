"""test_populate_car_results.py — cierra el hueco de la Fase 1 (RUNBOOK.md):
nada llamaba a compute_car() sobre eventos reales y guardaba el resultado.
Probado contra Postgres real con precios/factores sintéticos, siguiendo el
mismo patrón que test_enrichment.py / test_historical_analogues.py.
"""
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


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


def _seed(conn, n_days=320):
    rng = np.random.default_rng(3)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES ('1','TESTCO','Test Co','2021-01-01','2021-01-01')"
        )
        for i, d in enumerate(dates):
            price = 50.0 * (1 + 0.0003 * i + rng.normal(0, 0.01))
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning) "
                "VALUES ('TESTCO', %s, %s, 1.0, 100000, FALSE)",
                (d.date(), price),
            )
            cur.execute(
                "INSERT INTO fama_french_factors (trade_date, mkt_rf, smb, hml, rf) VALUES (%s,%s,%s,%s,%s)",
                (d.date(), float(rng.normal(0.0003, 0.008)), float(rng.normal(0, 0.004)), float(rng.normal(0, 0.004)), 0.00005),
            )
        d0 = dates[280].date()
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES ('1','TESTCO','EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],'acc1','https://x',%s,%s,'RULE',1.0,'h1')
            RETURNING event_id
            """,
            (d0, d0),
        )
        event_id = cur.fetchone()["event_id"]
    conn.commit()
    return event_id


def test_populate_stores_both_windows_for_evaluable_event(conn):
    from pipeline.backtest.populate_car_results import populate_missing_car_results

    event_id = _seed(conn)
    n = populate_missing_car_results(conn)
    assert n == 2  # ventanas de 5 y 20 días

    with conn.cursor() as cur:
        cur.execute("SELECT window_days, car FROM car_results WHERE event_id = %s ORDER BY window_days", (event_id,))
        rows = cur.fetchall()
    assert [r["window_days"] for r in rows] == [5, 20]


def test_populate_is_idempotent_and_skips_already_computed(conn):
    from pipeline.backtest.populate_car_results import populate_missing_car_results

    _seed(conn)
    first_run = populate_missing_car_results(conn)
    second_run = populate_missing_car_results(conn)
    assert first_run == 2
    assert second_run == 0  # ya no quedan eventos pendientes (LEFT JOIN ... IS NULL)


def test_populate_skips_events_without_enough_price_history(conn):
    from pipeline.backtest.populate_car_results import populate_missing_car_results

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES ('2','SHORTCO','Short Co','2024-01-01','2024-01-01')"
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES ('2','SHORTCO','EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],'acc2','https://x','2024-01-05','2024-01-05','RULE',1.0,'h2')
            """
        )
    conn.commit()
    # Sin ninguna fila en `prices` para SHORTCO -> debe saltarse sin crashear.
    n = populate_missing_car_results(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM car_results cr JOIN events e ON e.event_id=cr.event_id WHERE e.ticker='SHORTCO'")
        assert cur.fetchone()["n"] == 0


def test_eventos_sin_car_posible_no_bloquean_a_los_nuevos(conn):
    """Regresión: con limit=1 y un evento sin precios delante (CAR imposible
    para siempre), antes el evento evaluable de detrás no recibía CAR nunca."""
    from pipeline.backtest.populate_car_results import populate_missing_car_results

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES ('9','GHOST','Sin precios','2021-01-01','2021-01-01')"
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES ('9','GHOST','EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],'acc0','https://x',
                    '2021-06-01','2021-06-01','RULE',1.0,'h0')
            """
        )
    conn.commit()
    event_id = _seed(conn)
    assert populate_missing_car_results(conn, limit=1) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM car_results WHERE event_id = %s", (event_id,))
        assert cur.fetchone()["n"] == 2
