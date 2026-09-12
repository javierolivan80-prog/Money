"""test_sensitivity.py — Fase 6 PARTE 5. apply_extra_cost_bps y
apply_confidence_haircut son puros (cálculo a mano); apply_latency_sensitivity,
split_by_vix_regime, y run_sensitivity_analysis necesitan Postgres real."""
import os
from datetime import date, timedelta

import pytest

from pipeline.analyze.abstention_engine import CONFIDENCE_FLOOR
from pipeline.backtest.sensitivity import (
    CONFIDENCE_HAIRCUT_FRACTION,
    apply_confidence_haircut,
    apply_extra_cost_bps,
)

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


def _trade(pnl_pct, size=10_000.0, confidence=80.0, event_id=1):
    return {"event_id": event_id, "pnl_pct": pnl_pct, "position_size_dollars": size, "confidence": confidence}


# ---------------------------------------------------------------------------
# apply_extra_cost_bps — puro
# ---------------------------------------------------------------------------


def test_apply_extra_cost_bps_subtracts_from_pnl_pct():
    trades = [_trade(pnl_pct=2.0, size=10_000.0)]
    adjusted = apply_extra_cost_bps(trades, extra_bps=10.0)  # +0.1%
    assert adjusted[0]["pnl_pct"] == pytest.approx(1.9)
    assert adjusted[0]["pnl_abs"] == pytest.approx(10_000.0 * 0.019)


def test_apply_extra_cost_bps_can_flip_winner_to_loser():
    trades = [_trade(pnl_pct=0.15, size=5_000.0)]
    adjusted = apply_extra_cost_bps(trades, extra_bps=20.0)  # +0.2% (spread)
    assert adjusted[0]["pnl_pct"] == pytest.approx(-0.05)


# ---------------------------------------------------------------------------
# apply_confidence_haircut — puro
# ---------------------------------------------------------------------------


def test_confidence_haircut_excludes_trades_that_would_fall_below_floor():
    # confidence=45 -> 45*0.8=36 < 40 (CONFIDENCE_FLOOR) -> excluido
    # confidence=60 -> 60*0.8=48 >= 40 -> se mantiene
    trades = [_trade(pnl_pct=1.0, confidence=45.0, event_id=1), _trade(pnl_pct=1.0, confidence=60.0, event_id=2)]
    kept = apply_confidence_haircut(trades)
    assert [t["event_id"] for t in kept] == [2]


def test_confidence_haircut_boundary_exact_floor_is_kept():
    # confidence tal que confidence*0.8 == CONFIDENCE_FLOOR exactamente -> se mantiene (>=)
    boundary_confidence = CONFIDENCE_FLOOR / (1 - CONFIDENCE_HAIRCUT_FRACTION)
    trades = [_trade(pnl_pct=1.0, confidence=boundary_confidence, event_id=1)]
    kept = apply_confidence_haircut(trades)
    assert len(kept) == 1


def test_confidence_haircut_keeps_all_when_all_high_confidence():
    trades = [_trade(pnl_pct=1.0, confidence=90.0, event_id=i) for i in range(5)]
    kept = apply_confidence_haircut(trades)
    assert len(kept) == 5


# ---------------------------------------------------------------------------
# Integración contra Postgres real
# ---------------------------------------------------------------------------


@pytestmark_db
class TestSensitivityIntegration:
    @pytest.fixture
    def conn(self):
        from pipeline.db.connection import get_connection, init_schema

        c = get_connection()
        init_schema(c)
        with c.cursor() as cur:
            cur.execute(
                "TRUNCATE portfolio_trades, portfolio_equity_curve, portfolio_reports, car_results, "
                "backtest_runs, event_analyses, event_enrichment, events, prices, fama_french_factors, "
                "universe RESTART IDENTITY CASCADE"
            )
        c.commit()
        yield c
        c.close()

    def _business_days(self, start, n):
        days, d = [], start
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    def _seed(self, conn, cik, ticker, d0, cal, closes, decision, confidence, ev, vix_d0=20.0):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
                (cik, ticker, cal[0], cal[-1]),
            )
            for d, c in zip(cal, closes):
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) ON CONFLICT (ticker, trade_date) DO UPDATE SET "
                    "open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw",
                    (ticker, d, c, c, c * 1.01, c * 0.99),
                )
            cur.execute(
                """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES (%s,%s,'EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
                RETURNING event_id""",
                (cik, ticker, f"acc-{cik}", d0, d0, f"hash-{cik}"),
            )
            event_id = cur.fetchone()["event_id"]
            cur.execute(
                """INSERT INTO event_analyses (
                    event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                    judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                    n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                    abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                    trade_decision_balanced, model_version_bull_bear, model_version_judge
                ) VALUES (%s, 80, '{}', '{}', '{}', '{}', 0.6, %s, '{}', 25, '{}', %s, %s, %s, '{}', %s, %s, %s,
                    'claude-haiku-4-5', 'claude-sonnet-4-6')""",
                (event_id, confidence, ev, ev, ev, decision, decision, decision),
            )
            cur.execute(
                "INSERT INTO event_enrichment (event_id, vix_d0) VALUES (%s, %s) "
                "ON CONFLICT (event_id) DO UPDATE SET vix_d0 = EXCLUDED.vix_d0",
                (event_id, vix_d0),
            )
        conn.commit()
        return event_id

    def test_apply_latency_sensitivity_reprices_entry_to_d_plus_2(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio
        from pipeline.backtest.sensitivity import _fetch_trades_with_context, apply_latency_sensitivity

        d0 = date(2024, 3, 4)
        cal = self._business_days(d0, 15)
        closes = [100.0 + i * 0.3 for i in range(len(cal))]  # tendencia suave, sin TP/SL inmediato
        self._seed(conn, "lat1", "LAT1", d0, cal, closes, decision="LONG", confidence=80.0, ev=0.01)

        simulate_portfolio(conn, "CONSERVATIVE", "sens-test-1", starting_capital=100_000.0)
        trades = _fetch_trades_with_context(conn, "CONSERVATIVE", "sens-test-1")
        assert len(trades) == 1
        original_entry = float(trades[0]["entry_price"])

        adjusted = apply_latency_sensitivity(conn, trades)
        assert len(adjusted) == 1
        assert adjusted[0]["entry_price"] != pytest.approx(original_entry)
        # pnl_abs se recalcula de forma consistente con el nuevo pnl_pct.
        size = float(adjusted[0]["position_size_dollars"])
        assert adjusted[0]["pnl_abs"] == pytest.approx(size * (adjusted[0]["pnl_pct"] / 100))

    def test_split_by_vix_regime_divides_at_median(self, conn):
        from pipeline.backtest.portfolio_simulator import simulate_portfolio
        from pipeline.backtest.sensitivity import _fetch_trades_with_context, split_by_vix_regime

        cal = self._business_days(date(2024, 3, 4), 25)
        vix_values = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
        for i, vix in enumerate(vix_values):
            d0 = cal[i]
            ticker = f"VIX{i}"
            closes = [100.0 + j * 0.3 for j in range(len(cal))]
            self._seed(conn, f"vix{i}", ticker, d0, cal, closes, decision="LONG", confidence=80.0, ev=0.01, vix_d0=vix)

        simulate_portfolio(conn, "CONSERVATIVE", "sens-test-2", starting_capital=100_000.0)
        trades = _fetch_trades_with_context(conn, "CONSERVATIVE", "sens-test-2")
        assert len(trades) >= 4  # margen suficiente por encima del mínimo de split_by_vix_regime

        split = split_by_vix_regime(conn, trades)
        assert len(split["high_vix"]) + len(split["low_vix"]) == len(trades)
        assert len(split["high_vix"]) > 0 and len(split["low_vix"]) > 0

    def test_run_sensitivity_analysis_end_to_end(self, conn):
        from pipeline.backtest.portfolio_report import run_full_backtest
        from pipeline.backtest.sensitivity import run_sensitivity_analysis

        cal = self._business_days(date(2024, 3, 4), 60)
        for i in range(10):
            d0 = cal[i]
            ticker = f"E2E{i}"
            closes = [100.0 + i + j * 0.2 for j in range(len(cal))]
            self._seed(conn, f"e2e{i}", ticker, d0, cal, closes, decision="LONG", confidence=75.0, ev=0.01, vix_d0=15.0 + i)

        run_full_backtest(conn, run_batch_tag="sens-e2e-1", starting_capital=100_000.0)
        result = run_sensitivity_analysis(conn, "sens-e2e-1", starting_capital=100_000.0)

        assert set(result["scenarios"].keys()) == {"CONSERVATIVE", "AGGRESSIVE"}
        cons = result["scenarios"]["CONSERVATIVE"]
        assert "baseline" in cons
        assert "commission_plus_0.1pct" in cons
        # Más coste de transacción nunca puede mejorar el retorno total.
        if cons["baseline"]["n_trades"] > 0:
            assert cons["commission_plus_0.1pct"]["total_return"] <= cons["baseline"]["total_return"]
            assert cons["spread_plus_0.2pct"]["total_return"] <= cons["commission_plus_0.1pct"]["total_return"]
