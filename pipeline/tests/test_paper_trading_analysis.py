"""test_paper_trading_analysis.py — compute_prediction_accuracy (integración
real contra Postgres, necesita events+prices) y compute_alerts (puro)."""
import os
from datetime import date, timedelta

import pytest

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


# ---------------------------------------------------------------------------
# compute_alerts — puro, sin BD
# ---------------------------------------------------------------------------


def test_compute_alerts_flags_two_consecutive_losses():
    from pipeline.paper_trading.analysis import compute_alerts

    D0 = date(2024, 1, 2)
    closed_trades = [
        {"pnl_pct": 1.0, "exit_date": D0},
        {"pnl_pct": -1.0, "exit_date": D0 + timedelta(days=1)},
        {"pnl_pct": -1.0, "exit_date": D0 + timedelta(days=2)},
    ]
    alerts = compute_alerts(closed_trades, [], "CONSERVATIVE")
    warning = [a for a in alerts if a["type"] == "WARNING"]
    assert len(warning) == 1
    assert "overfitting" in warning[0]["message"]


def test_compute_alerts_no_warning_with_single_loss():
    from pipeline.paper_trading.analysis import compute_alerts

    closed_trades = [{"pnl_pct": 1.0, "exit_date": date(2024, 1, 2)}, {"pnl_pct": -1.0, "exit_date": date(2024, 1, 3)}]
    alerts = compute_alerts(closed_trades, [], "CONSERVATIVE")
    assert [a for a in alerts if a["type"] == "WARNING"] == []


def test_compute_alerts_congratulates_close_big_prediction():
    from pipeline.paper_trading.analysis import compute_alerts

    predictions = [
        {"event_id": 1, "ticker": "GOOD", "predicted_magnitude": 1.5, "actual_move_5d": 1.3, "error": 0.2},
        {"event_id": 2, "ticker": "BAD", "predicted_magnitude": 1.5, "actual_move_5d": -3.0, "error": 4.5},
        {"event_id": 3, "ticker": "SMALL", "predicted_magnitude": 0.3, "actual_move_5d": 0.31, "error": 0.01},  # magnitud pequeña, no cuenta
    ]
    alerts = compute_alerts([], predictions, "CONSERVATIVE")
    congrats = [a for a in alerts if a["type"] == "CONGRATULATE"]
    assert len(congrats) == 1
    assert congrats[0]["ticker"] == "GOOD"


def test_compute_alerts_ignores_predictions_without_known_outcome():
    from pipeline.paper_trading.analysis import compute_alerts

    predictions = [{"event_id": 1, "ticker": "PENDING", "predicted_magnitude": 5.0, "actual_move_5d": None, "error": None}]
    alerts = compute_alerts([], predictions, "CONSERVATIVE")
    assert alerts == []


# ---------------------------------------------------------------------------
# compute_prediction_accuracy — integración contra Postgres real
# ---------------------------------------------------------------------------


@pytestmark_db
class TestPredictionAccuracyIntegration:
    @pytest.fixture
    def conn(self):
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

    def _seed_price_series(self, conn, ticker, dates, closes):
        with conn.cursor() as cur:
            for d, c in zip(dates, closes):
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) "
                    "ON CONFLICT (ticker, trade_date) DO UPDATE SET open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw",
                    (ticker, d, c, c, c * 1.01, c * 0.99),
                )
        conn.commit()

    def _seed_event(self, conn, cik, ticker, d0, decision, net_conviction, confidence, ev):
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

    def test_computes_actual_move_5d_and_was_correct(self, conn):
        from pipeline.paper_trading.analysis import compute_prediction_accuracy

        d0 = date(2024, 3, 4)
        cal = [d0 + timedelta(days=i) for i in range(10) if (d0 + timedelta(days=i)).weekday() < 5]
        # Entrada D+1 = día 2 de cal, precio 100 -> sube constante.
        closes = [100.0 + i * 2.0 for i in range(len(cal))]
        self._seed_price_series(conn, "ACC1", cal, closes)
        self._seed_event(conn, "a1", "ACC1", d0, decision="LONG", net_conviction=0.6, confidence=77.0, ev=0.01)

        results = compute_prediction_accuracy(conn, "CONSERVATIVE", date(2024, 3, 4), date(2024, 3, 8))
        assert len(results) == 1
        r = results[0]
        assert r["predicted_direction"] == "LONG"
        assert r["predicted_magnitude"] == pytest.approx(1.0)  # ev=0.01 -> 1.0%
        assert r["actual_move_5d"] is not None
        assert r["actual_move_5d"] > 0  # precio subió, LONG -> correcto
        assert r["was_correct"] is True
        assert r["confidence_given"] == pytest.approx(77.0)
        assert r["was_traded"] is True

    def test_includes_no_trade_events_with_was_traded_false(self, conn):
        from pipeline.paper_trading.analysis import compute_prediction_accuracy

        d0 = date(2024, 3, 4)
        cal = [d0 + timedelta(days=i) for i in range(10) if (d0 + timedelta(days=i)).weekday() < 5]
        self._seed_price_series(conn, "NT1", cal, [100.0] * len(cal))
        self._seed_event(conn, "a2", "NT1", d0, decision="NO_TRADE", net_conviction=0.05, confidence=30.0, ev=0.0001)

        results = compute_prediction_accuracy(conn, "CONSERVATIVE", date(2024, 3, 4), date(2024, 3, 8))
        assert len(results) == 1
        assert results[0]["was_traded"] is False

    def test_actual_move_5d_is_none_when_insufficient_future_data(self, conn):
        from pipeline.paper_trading.analysis import compute_prediction_accuracy

        d0 = date(2024, 3, 4)
        # Solo 2 días tras D0 -> tras la entrada D+1 no hay 5 días de negociación.
        cal = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
        self._seed_price_series(conn, "PEND1", cal, [100.0, 100.5, 101.0])
        self._seed_event(conn, "a3", "PEND1", d0, decision="LONG", net_conviction=0.6, confidence=80.0, ev=0.01)

        results = compute_prediction_accuracy(conn, "CONSERVATIVE", date(2024, 3, 4), date(2024, 3, 8))
        assert results[0]["actual_move_5d"] is None
        assert results[0]["was_correct"] is None
        assert results[0]["error"] is None
