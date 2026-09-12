"""test_paper_trading_report.py — run_paper_trading_report de extremo a
extremo contra Postgres real: las 3 versiones, persistencia, y la
comparación con el backtest histórico."""
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
            "portfolio_reports, car_results, backtest_runs, event_analyses, event_enrichment, events, "
            "prices, fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _business_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _seed_price_series(conn, ticker: str, dates: list[date], closes: list[float]):
    with conn.cursor() as cur:
        for d, c in zip(dates, closes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) "
                "ON CONFLICT (ticker, trade_date) DO UPDATE SET open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw",
                (ticker, d, c, c, c * 1.01, c * 0.99),
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
            (cik, ticker, f"acc-{cik}", d0, d0, f"hash-{cik}"),
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


def test_run_paper_trading_report_end_to_end(conn):
    from pipeline.paper_trading.report import run_paper_trading_report

    week_start = date(2024, 3, 4)
    cal = _business_days(week_start, 15)  # cubre la semana simulada + margen para actual_move_5d

    # Un evento por cada día hábil de la semana objetivo (lun-vie = 5 días,
    # cal[0..4]) — cal[5] ya cae en la semana siguiente.
    for i in range(5):
        d0 = cal[i]
        ticker = f"PT{i}"
        closes = [100.0 + i + j * 0.5 for j in range(len(cal))]  # tendencia alcista suave
        _seed_price_series(conn, ticker, cal, closes)
        decision = "LONG" if i % 3 != 0 else "NO_TRADE"
        _seed_event(conn, f"pt{i}", ticker, d0, decision=decision, net_conviction=0.6, confidence=75.0, ev=0.01)
    conn.commit()

    report = run_paper_trading_report(conn, run_batch_tag="paper-test-1", as_of=date(2024, 3, 11))

    assert report["week_start"] == "2024-03-04"
    assert report["week_end"] == "2024-03-08"
    assert set(report["versions"].keys()) == {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}

    cons = report["versions"]["CONSERVATIVE"]
    assert cons["n_open_positions"] + cons["n_closed_trades"] > 0
    assert isinstance(cons["predictions"], list)
    assert len(cons["predictions"]) == 5  # TODOS los eventos de la semana, con y sin trade_decision
    assert any(p["was_traded"] is False for p in cons["predictions"])  # los NO_TRADE están incluidos
    assert "comparison_with_historical_backtest" in cons
    assert cons["comparison_with_historical_backtest"]["available"] is False  # sin backtest histórico corrido

    # Persistencia.
    with conn.cursor() as cur:
        cur.execute("SELECT report_json FROM paper_trading_reports WHERE run_batch_tag = %s", ("paper-test-1",))
        row = cur.fetchone()
    assert row is not None
    assert row["report_json"]["run_batch_tag"] == "paper-test-1"


def test_run_paper_trading_report_compares_against_existing_historical_backtest(conn):
    """Si ya hay un backtest histórico (portfolio_reports) para la misma
    versión, la comparación debe usarlo."""
    from pipeline.paper_trading.report import run_paper_trading_report

    week_start = date(2024, 3, 4)
    cal = _business_days(week_start, 15)
    for i in range(3):
        d0 = cal[i]
        ticker = f"CMP{i}"
        closes = [100.0 + j * 0.3 for j in range(len(cal))]
        _seed_price_series(conn, ticker, cal, closes)
        _seed_event(conn, f"cmp{i}", ticker, d0, decision="LONG", net_conviction=0.6, confidence=80.0, ev=0.01)
    conn.commit()

    # Un portfolio_reports "histórico" preexistente, fabricado a mano.
    import json

    fake_hist = {
        "run_batch_tag": "hist-1",
        "versions": {
            "CONSERVATIVE": {"trade_metrics": {"win_rate": 0.55}},
            "AGGRESSIVE": {"trade_metrics": {"win_rate": 0.40}},
            "BALANCED": {"trade_metrics": {"win_rate": 0.50}},
        },
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO portfolio_reports (run_batch_tag, report_json) VALUES ('hist-1', %s)",
            (json.dumps(fake_hist),),
        )
    conn.commit()

    report = run_paper_trading_report(conn, run_batch_tag="paper-test-2", as_of=date(2024, 3, 11))
    comparison = report["versions"]["CONSERVATIVE"]["comparison_with_historical_backtest"]
    assert comparison["available"] is True
    assert comparison["historical_win_rate"] == pytest.approx(0.55)


def test_open_position_unrealized_pnl_uses_latest_available_price(conn):
    from pipeline.paper_trading.report import run_paper_trading_report

    week_start = date(2024, 3, 4)
    d0 = week_start
    # Solo 2 días de precio tras la entrada -> queda OPEN. Movimiento
    # pequeño a propósito para NO cruzar el TP/SL de Conservative (+2%/-1.5%)
    # el único día de negociación disponible tras la entrada.
    cal = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
    _seed_price_series(conn, "OPENPNL", cal, [100.0, 102.0, 102.5])
    _seed_event(conn, "openpnl", "OPENPNL", d0, decision="LONG", net_conviction=0.6, confidence=80.0, ev=0.01)
    conn.commit()

    report = run_paper_trading_report(conn, run_batch_tag="paper-test-3", as_of=date(2024, 3, 11))
    cons = report["versions"]["CONSERVATIVE"]
    assert cons["n_open_positions"] == 1
    assert cons["n_closed_trades"] == 0
    pos = cons["open_positions"][0]
    assert pos["unrealized_pnl_pct"] is not None
    assert pos["unrealized_pnl_pct"] > 0  # entró a 102 (D+1), último precio disponible 102.5 -> positivo
