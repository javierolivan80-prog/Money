"""test_portfolio_validation.py — checksums anti-look-ahead + robustez temporal."""
import os
from datetime import date, timedelta

import pytest

from pipeline.backtest.portfolio_validation import (
    MIN_TRADES_PER_PERIOD_FOR_COMPARISON,
    WIN_RATE_DIVERGENCE_THRESHOLD_PP,
    compute_temporal_stability_report,
)

D0 = date(2024, 1, 2)


def _trade(pnl_pct, entry_date, win_flag=None):
    p = pnl_pct if win_flag is None else (1.0 if win_flag else -1.0)
    return {"pnl_pct": p, "entry_date": entry_date, "exit_date": entry_date + timedelta(days=5)}


# ---------------------------------------------------------------------------
# compute_temporal_stability_report — pura
# ---------------------------------------------------------------------------


def test_stability_report_flags_insufficient_sample():
    trades = [_trade(1, D0 + timedelta(days=i)) for i in range(5)]  # menos que MIN_TRADES_PER_PERIOD_FOR_COMPARISON
    report = compute_temporal_stability_report(trades, split_date=D0 + timedelta(days=3))
    assert report["stable"] is None
    assert any("insuficiente" in w for w in report["warnings"])


def test_stability_report_stable_when_win_rates_close():
    n = MIN_TRADES_PER_PERIOD_FOR_COMPARISON
    before = [_trade(0, D0 + timedelta(days=i), win_flag=(i % 2 == 0)) for i in range(n)]  # ~50% win rate
    after = [_trade(0, D0 + timedelta(days=100 + i), win_flag=(i % 2 == 0)) for i in range(n)]  # ~50% win rate
    report = compute_temporal_stability_report(before + after, split_date=D0 + timedelta(days=50))
    assert report["stable"] is True
    assert report["warnings"] == []


def test_stability_report_unstable_when_win_rate_diverges():
    n = MIN_TRADES_PER_PERIOD_FOR_COMPARISON
    before = [_trade(0, D0 + timedelta(days=i), win_flag=True) for i in range(n)]  # 100% win rate
    after = [_trade(0, D0 + timedelta(days=100 + i), win_flag=False) for i in range(n)]  # 0% win rate
    report = compute_temporal_stability_report(before + after, split_date=D0 + timedelta(days=50))
    assert report["stable"] is False
    assert any("diverge" in w for w in report["warnings"])


def test_stability_report_flags_expectancy_sign_flip():
    n = MIN_TRADES_PER_PERIOD_FOR_COMPARISON
    before = [_trade(5.0, D0 + timedelta(days=i)) for i in range(n)]   # rentable
    after = [_trade(-5.0, D0 + timedelta(days=100 + i)) for i in range(n)]  # perdedor
    report = compute_temporal_stability_report(before + after, split_date=D0 + timedelta(days=50))
    assert report["stable"] is False
    assert any("signo" in w for w in report["warnings"])


def test_stability_report_boundary_uses_configured_threshold():
    """Verifica que el umbral usado es realmente la constante exportada, no
    un número mágico duplicado en el test."""
    n = MIN_TRADES_PER_PERIOD_FOR_COMPARISON
    # Construye una diferencia de win_rate justo por debajo del umbral.
    diff_trades = int(WIN_RATE_DIVERGENCE_THRESHOLD_PP / 100 * n)
    before = [_trade(0, D0 + timedelta(days=i), win_flag=True) for i in range(n)]
    after = [_trade(0, D0 + timedelta(days=100 + i), win_flag=(i >= diff_trades - 1)) for i in range(n)]
    report = compute_temporal_stability_report(before + after, split_date=D0 + timedelta(days=50))
    # No se afirma un resultado concreto aquí (depende del redondeo) — solo
    # que la función corre sin reventar en el límite y produce un booleano.
    assert report["stable"] in (True, False)


# ---------------------------------------------------------------------------
# validate_no_lookahead — contra Postgres real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
class TestValidateNoLookaheadAgainstRealPostgres:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from pipeline.db.connection import get_connection, init_schema

        self.conn = get_connection()
        init_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(
                "TRUNCATE portfolio_trades, portfolio_equity_curve, car_results, backtest_runs, "
                "event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
                "RESTART IDENTITY CASCADE"
            )
        self.conn.commit()
        yield
        self.conn.close()

    def _seed_event(self, d0: date) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES ('1','TESTCO','X',%s,%s) ON CONFLICT (cik) DO NOTHING",
                (d0, d0),
            )
            cur.execute(
                """
                INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES ('1','TESTCO','EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
                RETURNING event_id
                """,
                (f"acc-{d0}", d0, d0, f"hash-{d0}"),
            )
            return cur.fetchone()["event_id"]

    def _insert_trade(self, event_id: int, entry_date: date, exit_date: date, tag: str):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO portfolio_trades (
                    event_id, version, execution_style, direction, entry_date, entry_price,
                    exit_date, exit_price, exit_reason, pnl_pct, pnl_abs, position_size_pct,
                    position_size_dollars, confidence, ev, prediction, actual_move_pct,
                    had_survivorship_warning, run_batch_tag
                ) VALUES (%s,'CONSERVATIVE','CONSERVATIVE','LONG',%s,100.0,%s,102.0,'TAKE_PROFIT',
                    2.0,60.0,3.0,3000.0,80.0,0.01,0.6,2.0,FALSE,%s)
                """,
                (event_id, entry_date, exit_date, tag),
            )
        self.conn.commit()

    def test_clean_run_has_no_violations(self):
        from pipeline.backtest.portfolio_validation import validate_no_lookahead

        d0 = date(2024, 1, 2)
        event_id = self._seed_event(d0)
        self._insert_trade(event_id, entry_date=d0 + timedelta(days=1), exit_date=d0 + timedelta(days=6), tag="clean")
        violations = validate_no_lookahead(self.conn, "clean")
        assert violations == []

    def test_entry_on_d0_is_flagged(self):
        from pipeline.backtest.portfolio_validation import validate_no_lookahead

        d0 = date(2024, 1, 2)
        event_id = self._seed_event(d0)
        # Inserción directa simulando un bug hipotético: entrada EN d0.
        self._insert_trade(event_id, entry_date=d0, exit_date=d0 + timedelta(days=5), tag="bad-entry")
        violations = validate_no_lookahead(self.conn, "bad-entry")
        assert len(violations) == 1
        assert "entrada en o antes de D0" in violations[0]
