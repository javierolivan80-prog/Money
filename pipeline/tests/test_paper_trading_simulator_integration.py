"""test_paper_trading_simulator_integration.py — simulate_paper_trading_week
de extremo a extremo contra Postgres real: fetch por semana, entrada D+1,
persistencia idempotente en paper_trades, y el caso OPEN cuando los datos
de precio no llegan hasta el fin de semana simulada."""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE paper_trades, paper_trading_reports, portfolio_trades, portfolio_equity_curve, "
            "car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
            "fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _seed_price_series(conn, ticker: str, dates: list[date], closes: list[float], opens=None):
    opens = opens or closes
    with conn.cursor() as cur:
        for d, o, c in zip(dates, opens, closes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) "
                "ON CONFLICT (ticker, trade_date) DO UPDATE SET open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw, "
                "high_raw=EXCLUDED.high_raw, low_raw=EXCLUDED.low_raw",
                (ticker, d, o, c, max(o, c) * 1.005, min(o, c) * 0.995),
            )
    conn.commit()


def _seed_event(conn, cik: str, ticker: str, d0: date, decision: str, net_conviction: float, confidence: float, ev: float) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, d0, d0),
        )
        cur.execute(
            """
            INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES (%s,%s,'EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
            RETURNING event_id
            """,
            (cik, ticker, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (%s, 80, '{}', '{}', '{}', '{}', %s, %s, '{}', 25, '{}', %s, %s, %s, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')
            """,
            (event_id, net_conviction, confidence, ev, ev, ev, decision, decision, decision),
        )
    conn.commit()
    return event_id


WEEK_START = date(2024, 3, 4)  # lunes
WEEK_END = date(2024, 3, 8)  # viernes


def _business_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def test_simulate_paper_trading_week_take_profit_and_persistence(conn):
    from pipeline.paper_trading.simulator import simulate_paper_trading_week

    d0 = date(2024, 3, 4)  # lunes -> entrada martes 5
    cal = _business_days(d0, 10)
    closes = [100.0, 100.5, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0]
    _seed_price_series(conn, "TP1", cal, closes)
    event_id = _seed_event(conn, "c1", "TP1", d0, decision="LONG", net_conviction=0.6, confidence=80.0, ev=0.01)

    results = simulate_paper_trading_week(conn, "CONSERVATIVE", WEEK_START, WEEK_END, run_batch_tag="ptw-1")

    assert len(results) == 1
    assert results[0]["event_id"] == event_id
    assert results[0]["status"] == "CLOSED_TP"

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM paper_trades WHERE run_batch_tag = %s", ("ptw-1",))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "CLOSED_TP"
    assert rows[0]["exit_date"] > rows[0]["entry_date"]

    # Re-ejecutar el mismo run_batch_tag no duplica (ON CONFLICT DO UPDATE).
    simulate_paper_trading_week(conn, "CONSERVATIVE", WEEK_START, WEEK_END, run_batch_tag="ptw-1")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM paper_trades WHERE run_batch_tag = %s", ("ptw-1",))
        assert cur.fetchone()["n"] == 1


def test_simulate_paper_trading_week_stays_open_when_price_data_incomplete(conn):
    """Si los precios solo llegan hasta mitad de la semana simulada (el caso
    real de correr esto un día laborable normal, no el viernes por la
    noche), la posición debe quedar OPEN en la BD — no debe inventarse un
    cierre con datos que todavía no existen."""
    from pipeline.paper_trading.simulator import simulate_paper_trading_week

    d0 = date(2024, 3, 4)
    # Solo 2 días de precio tras la entrada — la semana (hasta el viernes 8)
    # no está cubierta.
    cal = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
    closes = [100.0, 100.2, 100.3]  # sin moverse lo suficiente para TP/SL
    _seed_price_series(conn, "OPEN1", cal, closes)
    _seed_event(conn, "c2", "OPEN1", d0, decision="LONG", net_conviction=0.6, confidence=80.0, ev=0.01)

    results = simulate_paper_trading_week(conn, "CONSERVATIVE", WEEK_START, WEEK_END, run_batch_tag="ptw-2")

    assert len(results) == 1
    assert results[0]["status"] == "OPEN"
    assert results[0]["exit_date"] is None

    with conn.cursor() as cur:
        cur.execute("SELECT * FROM paper_trades WHERE run_batch_tag = %s", ("ptw-2",))
        row = cur.fetchall()[0]
    assert row["status"] == "OPEN"
    assert row["exit_date"] is None
    assert row["pnl_pct"] is None


def test_simulate_paper_trading_week_skips_events_with_no_trade_decision(conn):
    from pipeline.paper_trading.simulator import simulate_paper_trading_week

    d0 = date(2024, 3, 4)
    cal = _business_days(d0, 10)
    _seed_price_series(conn, "NT1", cal, [100.0] * 10)
    _seed_event(conn, "c3", "NT1", d0, decision="NO_TRADE", net_conviction=0.1, confidence=40.0, ev=0.0001)

    results = simulate_paper_trading_week(conn, "CONSERVATIVE", WEEK_START, WEEK_END, run_batch_tag="ptw-3")
    assert results == []


def test_simulate_paper_trading_week_no_lookahead_entry_strictly_after_d0(conn):
    from pipeline.paper_trading.simulator import simulate_paper_trading_week

    d0 = date(2024, 3, 4)
    cal = _business_days(d0, 10)
    _seed_price_series(conn, "LA1", cal, [100.0 + i * 0.1 for i in range(10)])
    _seed_event(conn, "c4", "LA1", d0, decision="LONG", net_conviction=0.6, confidence=80.0, ev=0.01)

    results = simulate_paper_trading_week(conn, "CONSERVATIVE", WEEK_START, WEEK_END, run_batch_tag="ptw-4")
    assert results[0]["entry_date"] > d0
