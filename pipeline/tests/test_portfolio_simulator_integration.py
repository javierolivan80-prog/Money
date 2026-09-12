"""test_portfolio_simulator_integration.py — simulate_portfolio() de extremo
a extremo contra Postgres real, con precios sintéticos diseñados para
disparar cada mecanismo de salida al menos una vez (TP, SL, max_holding,
trailing_stop) y verificar la reconciliación de caja/equity.
"""
import os
from datetime import date, timedelta

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
            "TRUNCATE portfolio_trades, portfolio_equity_curve, car_results, backtest_runs, "
            "event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
            "RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _seed_price_series(conn, ticker: str, dates: list[date], closes: list[float], opens: list[float] | None = None):
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


def _seed_event_with_analysis(
    conn, cik: str, ticker: str, d0: date,
    trade_decision_conservative: str, trade_decision_aggressive: str, trade_decision_balanced: str,
    net_conviction: float, confidence: float,
    ev_conservative: float, ev_aggressive: float, ev_balanced: float,
) -> int:
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
            ) VALUES (%s, 80, '{}', '{}', '{}', '{}', %s, %s, '{}', 20, '{}', %s, %s, %s, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')
            """,
            (event_id, net_conviction, confidence, ev_conservative, ev_aggressive, ev_balanced,
             trade_decision_conservative, trade_decision_aggressive, trade_decision_balanced),
        )
    conn.commit()
    return event_id


def _business_days(start: date, n: int) -> list[date]:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class TestConservativeTakeProfitPath:
    def test_conservative_trade_hits_take_profit_and_credits_cash(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        # d0 en business day 0; entrada en business day 1 (apertura); TP a +2% al día siguiente.
        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.8, confidence=90.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        closes = [100.0] + [100.0] + [105.0] * (len(cal) - 2)  # salto a +5% en la 2ª vela tras entrada -> dispara TP (+2%)
        _seed_price_series(conn, "TESTCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-1", starting_capital=100_000.0)
        assert result["n_trades"] == 1

        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-1'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "TAKE_PROFIT"
        assert trade["direction"] == "LONG"
        assert float(trade["pnl_pct"]) > 0
        assert trade["entry_date"] > d0  # checksum anti-look-ahead: nunca entra en D0

        # Verifica reconciliación de caja: balance final = capital inicial + pnl_abs de todos los trades.
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM portfolio_equity_curve WHERE version='CONSERVATIVE' AND run_batch_tag='test-1' ORDER BY trade_date DESC LIMIT 1")
            final_balance = float(cur.fetchone()["balance"])
        assert final_balance == pytest.approx(100_000.0 + float(trade["pnl_abs"]), rel=1e-6)


class TestConservativeStopLossPath:
    def test_conservative_trade_hits_stop_loss(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.75, confidence=85.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        closes = [100.0, 100.0] + [95.0] * (len(cal) - 2)  # cae -5% -> dispara SL (-1.5%)
        _seed_price_series(conn, "TESTCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-2", starting_capital=100_000.0)
        assert result["n_trades"] == 1
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM portfolio_trades WHERE run_batch_tag='test-2'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "STOP_LOSS"
        assert float(trade["pnl_pct"]) < 0


class TestMaxConcurrentEnforced:
    def test_fourth_conservative_signal_is_dropped_when_three_slots_full(self, conn):
        """max_concurrent=3 para Conservative — una 4ª señal el mismo día no
        debe abrir posición."""
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 30)
        d0 = cal[0]
        flat_closes = [100.0] * len(cal)  # sin movimiento: nada dispara TP/SL, todo cierra por max_holding
        for i in range(4):
            _seed_event_with_analysis(
                conn, str(i), f"T{i}", d0,
                trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
                net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
            )
            _seed_price_series(conn, f"T{i}", cal, flat_closes)

        result = simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-3", starting_capital=100_000.0)
        assert result["n_trades"] == 3  # la 4ª señal se descartó por falta de hueco


class TestBalancedExecutionStyleSplit:
    def test_balanced_trade_uses_conservative_style_sizing_when_high_confidence(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 15)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="LONG",
            net_conviction=0.8, confidence=90.0,  # >= 70 y ev_conservative >= 0.002 -> estilo CONSERVATIVE
            ev_conservative=0.01, ev_aggressive=0.01, ev_balanced=0.01,
        )
        flat_closes = [100.0] * len(cal)
        _seed_price_series(conn, "TESTCO", cal, flat_closes)

        simulate_portfolio(conn, "BALANCED", run_batch_tag="test-4", starting_capital=100_000.0)
        with conn.cursor() as cur:
            cur.execute("SELECT execution_style, position_size_pct FROM portfolio_trades WHERE run_batch_tag='test-4'")
            trade = cur.fetchone()
        assert trade["execution_style"] == "CONSERVATIVE"
        assert float(trade["position_size_pct"]) == pytest.approx(1.5)  # tamaño fijo de Balanced-Conservative


class TestAggressiveTrailingStopPath:
    def test_aggressive_trade_partially_closes_via_trailing_stop(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 30)
        d0 = cal[0]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="NO_TRADE", trade_decision_aggressive="LONG", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=60.0, ev_conservative=0.0, ev_aggressive=0.01, ev_balanced=0.0,
        )
        # Entrada en cal[1] a 100. Sube gradualmente hasta +65% para cruzar
        # los tramos de trailing stop de 20/40/60, luego se mantiene plana.
        closes = [100.0, 100.0]
        ramp = [110.0, 122.0, 135.0, 148.0, 162.0, 168.0]
        closes += ramp
        closes += [165.0] * (len(cal) - len(closes))
        _seed_price_series(conn, "TESTCO", cal, closes, opens=closes)

        result = simulate_portfolio(conn, "AGGRESSIVE", run_batch_tag="test-6", starting_capital=100_000.0)
        assert result["n_trades"] == 1
        with conn.cursor() as cur:
            cur.execute("SELECT exit_reason, pnl_pct FROM portfolio_trades WHERE run_batch_tag='test-6'")
            trade = cur.fetchone()
        assert trade["exit_reason"] == "TRAILING_STOP"
        assert float(trade["pnl_pct"]) > 0  # una subida sostenida debe dar ganancia neta


class TestNoLookaheadChecksum:
    def test_all_trades_satisfy_exit_after_entry_and_entry_after_d0(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio

        cal = _business_days(date(2024, 1, 2), 40)
        d0 = cal[5]
        _seed_event_with_analysis(
            conn, "1", "TESTCO", d0,
            trade_decision_conservative="LONG", trade_decision_aggressive="NO_TRADE", trade_decision_balanced="NO_TRADE",
            net_conviction=0.7, confidence=80.0, ev_conservative=0.01, ev_aggressive=0.0, ev_balanced=0.0,
        )
        flat_closes = [100.0] * len(cal)
        _seed_price_series(conn, "TESTCO", cal, flat_closes)

        simulate_portfolio(conn, "CONSERVATIVE", run_batch_tag="test-5", starting_capital=100_000.0)
        with conn.cursor() as cur:
            cur.execute("SELECT entry_date, exit_date FROM portfolio_trades WHERE run_batch_tag='test-5'")
            trade = cur.fetchone()
        assert trade["entry_date"] > d0
        assert trade["exit_date"] > trade["entry_date"]
