"""connection.py — capa mínima de acceso a Postgres.

Sin ORM (ARCHITECTURE_LEAN.md §2: "sin ORM"). psycopg directo, SQL explícito.
Todo INSERT es idempotente vía ON CONFLICT, porque el pipeline se reejecuta
cada noche y por reintentos ante fallos parciales.
"""
from __future__ import annotations

import logging
from typing import Callable

import psycopg
from psycopg.rows import dict_row

from pipeline import config
from pipeline.ingest.ticker_map import resolve as resolve_ticker

logger = logging.getLogger(__name__)


def get_connection() -> psycopg.Connection:
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=False)


def init_schema(conn: psycopg.Connection) -> None:
    """Aplica schema.sql. Idempotente por los IF NOT EXISTS del propio DDL."""
    from pathlib import Path

    schema_sql = (Path(__file__).parent / "schema.sql").read_text()
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()
    logger.info("Schema aplicado")


def upsert_universe_entries(conn: psycopg.Connection, filings: list) -> None:
    """Registra/actualiza cada CIK visto en universe. No decide investabilidad
    aquí (eso requiere market cap/ADV, que vienen de prices — se calcula
    después, en un paso de mantenimiento de universo separado).
    """
    with conn.cursor() as cur:
        for f in filings:
            ticker = resolve_ticker(f.cik)
            if ticker is None:
                # CIK sin ticker cotizado (fondo, insider individual, etc.) — no
                # pertenece al universo invertible. Se registra igual con ticker
                # NULL para no perder el evento silenciosamente, pero
                # in_investable_universe se queda en FALSE (default) y el
                # backtester debe ignorar estas filas.
                logger.debug("Sin ticker para CIK %s (%s), se omite de universe", f.cik, f.company_name)
                continue
            cur.execute(
                """
                INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date)
                VALUES (%(cik)s, %(ticker)s, %(company_name)s, %(d)s, %(d)s)
                ON CONFLICT (cik) DO UPDATE SET
                    last_seen_date = GREATEST(universe.last_seen_date, EXCLUDED.last_seen_date),
                    ticker = EXCLUDED.ticker,
                    company_name = EXCLUDED.company_name
                """,
                {
                    "cik": f.cik,
                    "ticker": ticker,
                    "company_name": f.company_name,
                    "d": f.filed_at.date(),
                },
            )
    conn.commit()


def upsert_events(
    conn: psycopg.Connection,
    filings: list,
    classify_fn: Callable,
    d0_fn: Callable,
) -> int:
    """Inserta un evento por cada (filing, event_class) relevante.
    UNIQUE(source, accession_number, event_class) hace esto idempotente:
    reejecutar el mismo día no duplica filas, solo no-opea en el ON CONFLICT.
    """
    count = 0
    with conn.cursor() as cur:
        for f in filings:
            ticker = resolve_ticker(f.cik)
            if ticker is None:
                continue  # ver nota en upsert_universe_entries: fuera del universo invertible
            for event_class in classify_fn(f.item_codes):
                cur.execute(
                    """
                    INSERT INTO events (
                        cik, ticker, source, is_satellite, event_class, item_codes,
                        accession_number, source_url, filed_at, d0_close_date,
                        classification_method, classification_confidence, raw_text_hash
                    ) VALUES (
                        %(cik)s, %(ticker)s, 'EDGAR', FALSE, %(event_class)s, %(item_codes)s,
                        %(accession)s, %(url)s, %(filed_at)s, %(d0)s,
                        'RULE', 1.0, %(hash)s
                    )
                    ON CONFLICT (source, accession_number, event_class) DO NOTHING
                    """,
                    {
                        "cik": f.cik,
                        "ticker": ticker,
                        "event_class": event_class,
                        "item_codes": f.item_codes,
                        "accession": f.accession_number,
                        "url": f.source_url,
                        "filed_at": f.filed_at,
                        "d0": d0_fn(f.filed_at),
                        "hash": f.raw_text_hash,
                    },
                )
                count += cur.rowcount
    conn.commit()
    return count
