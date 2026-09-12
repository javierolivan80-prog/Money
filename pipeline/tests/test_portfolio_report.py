"""test_portfolio_report.py — el ensamblado final, de extremo a extremo
contra Postgres real: las 3 versiones, el reporte completo, y la
recomendación.
"""
import os
from datetime import date, timedelta

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE portfolio_trades, portfolio_equity_curve, car_results, backtest_runs, "
            "event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
            "RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _business_days(start: date, n: int) -> list[date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _seed_ticker_prices(conn, ticker: str, dates: list[date], closes: list[float]):
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
            ) VALUES (%s, 80, '{}', '{}', '{}', '{}', %s, %s, '{}', 25, '{}', %s, %s, %s, '{}', %s, 'NO_TRADE', 'NO_TRADE',
                'claude-haiku-4-5', 'claude-sonnet-4-6')
            """,
            (event_id, net_conviction, confidence, ev, ev, ev, decision),
        )
    conn.commit()
    return event_id


def test_run_full_backtest_end_to_end_produces_complete_report(conn):
    from pipeline.backtest.portfolio_report import run_full_backtest

    cal = _business_days(date(2022, 1, 3), 120)
    rng = np.random.default_rng(11)

    # 25 eventos Conservative con una deriva ligeramente positiva -> algunos
    # ganan (TP), algunos pierden (SL), la mayoría cierra por max_holding.
    for i in range(25):
        d0 = cal[i]
        ticker = f"T{i}"
        drift = rng.normal(0.0015, 0.01, len(cal))
        closes = list(100.0 * np.exp(np.cumsum(drift)))
        _seed_ticker_prices(conn, ticker, cal, closes)
        _seed_event(conn, str(i), ticker, d0, decision="LONG", net_conviction=0.7, confidence=80.0, ev=0.01)

    # Factores FF3 para el risk-free de compute_equity_metrics.
    with conn.cursor() as cur:
        for d in cal:
            cur.execute(
                "INSERT INTO fama_french_factors (trade_date, mkt_rf, smb, hml, rf) VALUES (%s,0.0003,0.0001,-0.0001,0.00005) "
                "ON CONFLICT (trade_date) DO NOTHING",
                (d,),
            )
    conn.commit()

    report = run_full_backtest(conn, run_batch_tag="full-test-1", starting_capital=100_000.0)

    assert set(report["versions"].keys()) == {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    cons = report["versions"]["CONSERVATIVE"]
    assert cons["trade_metrics"]["total_trades"] > 0
    assert cons["no_lookahead_violations"] == []
    assert "recommendation" not in cons  # la recomendación es del reporte global, no por versión

    assert report["recommendation"]["verdict"] in (
        "NO todavía",
        "SÍ, con capital de prueba pequeño y solo en la(s) versión(es) que superan los 3 criterios",
    )
    assert len(report["recommendation"]["findings"]) == 3  # una entrada por versión

    assert report["bias_report"]["n_total_tickers"] == 25

    # Curva de equity presente y con longitud > 0 para la versión con trades.
    assert len(cons["equity_curve"]) > 0
    # Top winners/losers no deben reventar aunque haya pocos trades.
    assert isinstance(cons["top_10_winners"], list)
    assert isinstance(cons["top_10_losers"], list)


def test_generate_recommendation_flags_lookahead_violations_as_blocking():
    from pipeline.backtest.portfolio_report import generate_recommendation

    fake_report = {
        "CONSERVATIVE": {
            "no_lookahead_violations": ["algo violó anti-look-ahead"],
            "trade_metrics": {"total_trades": 50},
            "equity_metrics": {"sharpe_ratio": 2.0, "max_drawdown": 0.05},
            "calibration": {"calibration_score": 0.9},
        }
    }
    result = generate_recommendation(fake_report)
    assert result["verdict"] == "NO todavía"
    assert "violación" in result["findings"][0]


def test_generate_recommendation_says_yes_when_all_three_criteria_pass():
    from pipeline.backtest.portfolio_report import generate_recommendation

    fake_report = {
        "CONSERVATIVE": {
            "no_lookahead_violations": [],
            "trade_metrics": {"total_trades": 50},
            "equity_metrics": {"sharpe_ratio": 1.5, "max_drawdown": 0.10},
            "calibration": {"calibration_score": 0.75},
        }
    }
    result = generate_recommendation(fake_report)
    assert result["verdict"].startswith("SÍ")
