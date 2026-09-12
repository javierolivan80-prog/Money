"""test_db_integration.py — pruebas de integración contra Postgres real.

A diferencia de los tests de parseo (fixtures offline), estos SÍ ejecutan SQL
real contra una base de datos real. Verificado en esta sesión contra un
Postgres 16 local (el sandbox no tiene salida a EDGAR/yfinance, pero sí tiene
`psql`/Postgres instalados localmente — no hace falta red para esto).

Requiere DATABASE_URL apuntando a una base vacía o de test. Se salta
automáticamente si la variable no está definida (por ejemplo, en una máquina
sin Postgres). El workflow de GitHub Actions (.github/workflows/nightly_pipeline.yml)
levanta un servicio Postgres efímero para correr esto en CI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida — se salta test de integración"
)


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    # Limpieza entre tests: TRUNCATE en orden que respeta FKs.
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE placebo_runs, backtest_runs, event_analyses, event_enrichment, events, prices, "
            "fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


@dataclass
class FakeFiling:
    accession_number: str
    cik: str
    company_name: str
    form_type: str
    filed_at: datetime
    item_codes: list
    source_url: str
    raw_text_hash: str


def test_upsert_universe_and_events_roundtrip(conn, monkeypatch):
    """CIK->ticker se mockea aquí: resolver contra el EDGAR real está fuera de
    alcance sin red, pero ESO no es lo que este test valida. Este test valida
    que el SQL de upsert (columnas, tipos, constraints, ON CONFLICT) es correcto
    contra un Postgres real — algo que ninguna revisión estática garantiza."""
    monkeypatch.setattr("pipeline.db.connection.resolve_ticker", lambda cik: "ACME" if cik == "1234567" else None)

    from pipeline.db.connection import upsert_events, upsert_universe_entries
    from pipeline.ingest.edgar_scraper import classify_event_classes, compute_d0_close_date

    filing = FakeFiling(
        accession_number="0001234567-24-000123",
        cik="1234567",
        company_name="ACME WIDGETS CORP",
        form_type="8-K",
        filed_at=datetime(2024, 3, 15, 9, 0),
        item_codes=["2.02", "9.01"],
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/0001234567-24-000123.txt",
        raw_text_hash="deadbeef",
    )

    upsert_universe_entries(conn, [filing])
    inserted = upsert_events(conn, [filing], classify_event_classes, compute_d0_close_date)
    assert inserted == 1  # solo 2.02 genera evento; 9.01 no es clase de evento

    with conn.cursor() as cur:
        cur.execute("SELECT ticker, company_name FROM universe WHERE cik = '1234567'")
        row = cur.fetchone()
        assert row["ticker"] == "ACME"

        cur.execute("SELECT event_class, d0_close_date, ticker FROM events WHERE cik = '1234567'")
        row = cur.fetchone()
        assert row["event_class"] == "8K_2.02_EARNINGS"
        assert row["d0_close_date"] == date(2024, 3, 15)
        assert row["ticker"] == "ACME"


def test_upsert_events_is_idempotent_on_rerun(conn, monkeypatch):
    """Reejecutar el mismo día no debe duplicar eventos (UNIQUE constraint +
    ON CONFLICT DO NOTHING). Esto es lo que hace seguro reintentar tras un
    fallo parcial del batch nocturno."""
    monkeypatch.setattr("pipeline.db.connection.resolve_ticker", lambda cik: "ACME")

    from pipeline.db.connection import upsert_events, upsert_universe_entries
    from pipeline.ingest.edgar_scraper import classify_event_classes, compute_d0_close_date

    filing = FakeFiling(
        accession_number="0001234567-24-000123",
        cik="1234567",
        company_name="ACME WIDGETS CORP",
        form_type="8-K",
        filed_at=datetime(2024, 3, 15, 9, 0),
        item_codes=["2.02"],
        source_url="https://example.com",
        raw_text_hash="deadbeef",
    )
    upsert_universe_entries(conn, [filing])
    upsert_events(conn, [filing], classify_event_classes, compute_d0_close_date)
    upsert_events(conn, [filing], classify_event_classes, compute_d0_close_date)  # rerun

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM events WHERE cik = '1234567'")
        assert cur.fetchone()["n"] == 1


def test_backtest_runs_no_lookahead_constraint_is_enforced(conn, monkeypatch):
    """El CHECK constraint chk_no_lookahead_5d/20d debe rechazar a nivel de BD
    cualquier intento de registrar una salida en o antes de la entrada — defensa
    en profundidad más allá de la disciplina del código Python (ARCHITECTURE_LEAN.md
    §8, T3)."""
    monkeypatch.setattr("pipeline.db.connection.resolve_ticker", lambda cik: "ACME")
    from pipeline.db.connection import upsert_events, upsert_universe_entries
    from pipeline.ingest.edgar_scraper import classify_event_classes, compute_d0_close_date

    filing = FakeFiling(
        accession_number="0001234567-24-000123",
        cik="1234567",
        company_name="ACME",
        form_type="8-K",
        filed_at=datetime(2024, 3, 15, 9, 0),
        item_codes=["2.02"],
        source_url="https://example.com",
        raw_text_hash="deadbeef",
    )
    upsert_universe_entries(conn, [filing])
    upsert_events(conn, [filing], classify_event_classes, compute_d0_close_date)

    with conn.cursor() as cur:
        cur.execute("SELECT event_id FROM events LIMIT 1")
        event_id = cur.fetchone()["event_id"]

    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO backtest_runs (
                    event_id, strategy_version, entry_date, exit_date_5d,
                    predicted_direction, predicted_ev_pct, run_batch_tag
                ) VALUES (%s, 'CONSERVATIVE', '2024-03-18', '2024-03-18', 'LONG', 1.5, 'test-run')
                """,
                (event_id,),
            )
    conn.rollback()


def test_price_gap_detection_flags_missing_trading_days(conn):
    """Prueba end-to-end de _store_with_gap_detection contra Postgres real:
    un DataFrame con un hueco en medio del rango debe producir filas
    survivorship_warning=TRUE exactamente en los días faltantes, y FALSE en
    los presentes. Ningún precio se inventa para el hueco (NULL, no interpolado)."""
    from pipeline.ingest.yfinance_backfill import _store_with_gap_detection, _trading_days_expected

    dates_present = pd.to_datetime(["2024-03-11", "2024-03-12", "2024-03-14", "2024-03-15"])  # falta el 13 (miércoles)
    df = pd.DataFrame(
        {
            "Open": [9.9, 10.4, 10.9, 11.1],    # añadida en el backtest de cartera (entrada = apertura D+1)
            "Close": [10.0, 10.5, 11.0, 11.2],
            "Adj Close": [9.5, 10.0, 10.5, 10.7],
            "High": [10.2, 10.7, 11.3, 11.4],   # añadidas en Fase 2 (proxy de spread — ver schema.sql)
            "Low": [9.8, 10.2, 10.8, 11.0],
            "Volume": [1000, 1100, 1200, 1300],
        },
        index=dates_present,
    )
    expected = _trading_days_expected(date(2024, 3, 11), date(2024, 3, 15))
    _store_with_gap_detection(conn, "GAPTEST", df, expected)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, close_raw, survivorship_warning FROM prices "
            "WHERE ticker = 'GAPTEST' ORDER BY trade_date"
        )
        rows = cur.fetchall()

    by_date = {r["trade_date"]: r for r in rows}
    assert by_date[date(2024, 3, 13)]["survivorship_warning"] is True
    assert by_date[date(2024, 3, 13)]["close_raw"] is None  # no se inventa el precio
    assert by_date[date(2024, 3, 11)]["survivorship_warning"] is False
    assert by_date[date(2024, 3, 11)]["close_raw"] == 10.0
