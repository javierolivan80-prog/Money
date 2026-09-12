"""test_portfolio_metrics.py — cada fórmula verificada contra un cálculo a
mano con números redondos, para poder confiar en que no hay un error de
signo o de denominador (el tipo de bug más fácil de colar en una fórmula
financiera y más difícil de notar a simple vista)."""
from datetime import date, timedelta

import pytest

from pipeline.backtest.portfolio_metrics import (
    CALIBRATION_TARGET,
    INSUFFICIENT_SAMPLE_THRESHOLD,
    compute_asymmetry_report,
    compute_calibration,
    compute_equity_metrics,
    compute_metrics_by_event_type,
    compute_prediction_regression,
    compute_trade_metrics,
    top_n_trades,
)

D0 = date(2024, 1, 2)


def _trade(pnl_pct, exit_date=None, event_class="8K_2.02_EARNINGS", ev=0.005, actual_move_pct=None):
    return {
        "pnl_pct": pnl_pct,
        "exit_date": exit_date or D0,
        "event_class": event_class,
        "ev": ev,
        "actual_move_pct": actual_move_pct if actual_move_pct is not None else pnl_pct,
    }


# ---------------------------------------------------------------------------
# compute_trade_metrics
# ---------------------------------------------------------------------------


def test_trade_metrics_empty_list():
    m = compute_trade_metrics([])
    assert m["total_trades"] == 0
    assert m["win_rate"] is None


def test_trade_metrics_hand_calculated_example():
    # 4 ganadores (+2,+4,+6,+8 => gross_profit=20, avg=5), 2 perdedores (-1,-3 => gross_loss=-4, avg=-2)
    trades = [_trade(p) for p in [2, 4, 6, 8, -1, -3]]
    m = compute_trade_metrics(trades)
    assert m["total_trades"] == 6
    assert m["winning_trades"] == 4
    assert m["losing_trades"] == 2
    assert m["win_rate"] == pytest.approx(4 / 6)
    assert m["profit_factor"] == pytest.approx(20 / 4)  # gross_profit / |gross_loss|
    assert m["avg_winner"] == pytest.approx(5.0)
    assert m["avg_loser"] == pytest.approx(-2.0)
    # expectancy = win_rate*avg_winner - (1-win_rate)*|avg_loser| = (4/6)*5 - (2/6)*2
    assert m["expectancy"] == pytest.approx((4 / 6) * 5 - (2 / 6) * 2)
    assert m["largest_win"] == 8
    assert m["largest_loss"] == -3


def test_trade_metrics_zero_pnl_counts_as_loser():
    trades = [_trade(0.0), _trade(5.0)]
    m = compute_trade_metrics(trades)
    assert m["winning_trades"] == 1
    assert m["losing_trades"] == 1


def test_trade_metrics_no_losers_gives_none_profit_factor_not_infinity():
    trades = [_trade(2), _trade(3)]
    m = compute_trade_metrics(trades)
    assert m["profit_factor"] is None  # no se inventa infinito
    assert m["avg_loser"] is None


def test_trade_metrics_consecutive_streaks_chronological():
    # En orden cronológico: W,W,W,L,L,W,L,L,L,L -> max win streak=3, max loss streak=4
    dates = [D0 + timedelta(days=i) for i in range(10)]
    pnls = [1, 1, 1, -1, -1, 1, -1, -1, -1, -1]
    trades = [_trade(p, exit_date=d) for p, d in zip(pnls, dates)]
    m = compute_trade_metrics(trades)
    assert m["consecutive_wins"] == 3
    assert m["consecutive_losses"] == 4


def test_trade_metrics_streaks_use_exit_date_order_not_input_order():
    """Si los trades llegan desordenados, las rachas deben calcularse por
    exit_date, no por el orden de la lista."""
    trades = [
        _trade(1, exit_date=D0 + timedelta(days=2)),
        _trade(-1, exit_date=D0),
        _trade(1, exit_date=D0 + timedelta(days=1)),
    ]
    m = compute_trade_metrics(trades)
    # Cronológico real: L, W, W -> racha de victorias = 2
    assert m["consecutive_wins"] == 2


# ---------------------------------------------------------------------------
# compute_equity_metrics
# ---------------------------------------------------------------------------


def test_equity_metrics_total_and_annual_return():
    curve = [
        {"trade_date": date(2024, 1, 1), "balance": 100_000.0},
        {"trade_date": date(2024, 1, 1) + timedelta(days=365), "balance": 110_000.0},
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["total_return"] == pytest.approx(0.10)
    assert m["annual_return"] == pytest.approx(0.10, abs=0.001)  # ~1 año exacto


def test_equity_metrics_max_drawdown_from_peak():
    curve = [
        {"trade_date": date(2024, 1, i + 1), "balance": b}
        for i, b in enumerate([100_000, 120_000, 90_000, 95_000, 130_000])
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    # Peor caída: de 120k a 90k = -25%
    assert m["max_drawdown"] == pytest.approx(0.25)


def test_equity_metrics_empty_curve_returns_all_none():
    m = compute_equity_metrics([], starting_capital=100_000.0)
    assert all(v is None for v in m.values())


def test_equity_metrics_sharpe_positive_for_steady_gains():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    balances = [100_000 * (1.001**i) for i in range(30)]  # crecimiento constante, sin volatilidad
    curve = [{"trade_date": d, "balance": b} for d, b in zip(dates, balances)]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["sharpe_ratio"] > 0


def test_equity_metrics_calmar_and_recovery_factor_use_drawdown():
    curve = [
        {"trade_date": date(2024, 1, i + 1), "balance": b}
        for i, b in enumerate([100_000, 150_000, 120_000])  # dd desde 150k a 120k = -20%
    ]
    m = compute_equity_metrics(curve, starting_capital=100_000.0)
    assert m["calmar_ratio"] == pytest.approx(m["annual_return"] / m["max_drawdown"])
    assert m["recovery_factor"] == pytest.approx(m["total_return"] / m["max_drawdown"])


def test_equity_metrics_risk_free_rate_reduces_sharpe():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    balances = [100_000 * (1.001**i) for i in range(30)]
    curve = [{"trade_date": d, "balance": b} for d, b in zip(dates, balances)]
    rf = {d: 0.0015 for d in dates}  # tasa libre de riesgo MAYOR que el retorno diario de la cartera
    no_rf = compute_equity_metrics(curve, 100_000.0)
    with_rf = compute_equity_metrics(curve, 100_000.0, risk_free_daily=rf)
    assert with_rf["sharpe_ratio"] < no_rf["sharpe_ratio"]


# ---------------------------------------------------------------------------
# compute_metrics_by_event_type
# ---------------------------------------------------------------------------


def test_metrics_by_event_type_groups_correctly():
    trades = [_trade(5, event_class="A")] * 3 + [_trade(-2, event_class="B")] * 25
    result = compute_metrics_by_event_type(trades)
    assert result["A"]["n_trades"] == 3
    assert result["A"]["insufficient_sample"] is True  # 3 < 20
    assert result["B"]["n_trades"] == 25
    assert result["B"]["insufficient_sample"] is False


def test_metrics_by_event_type_threshold_boundary():
    trades = [_trade(1, event_class="X")] * INSUFFICIENT_SAMPLE_THRESHOLD
    result = compute_metrics_by_event_type(trades)
    assert result["X"]["insufficient_sample"] is False  # exactamente 20, no "< 20"


# ---------------------------------------------------------------------------
# compute_asymmetry_report
# ---------------------------------------------------------------------------


def test_asymmetry_report_detects_favoring_gains():
    trades = [_trade(0, actual_move_pct=m) for m in [4, 5, 6, -1, -2]]  # 3/5 llegan a +3%, 0/5 a -3%
    report = compute_asymmetry_report(trades, threshold_pct=3.0)
    assert report["pct_reaching_positive_threshold"] == pytest.approx(60.0)
    assert report["pct_reaching_negative_threshold"] == pytest.approx(0.0)
    assert report["asymmetric_favoring_gains"] is True


def test_asymmetry_report_empty_trades():
    report = compute_asymmetry_report([])
    assert report["n_trades"] == 0
    assert report["asymmetric_favoring_gains"] is None


# ---------------------------------------------------------------------------
# compute_calibration
# ---------------------------------------------------------------------------


def test_calibration_perfect_prediction_gives_score_one():
    trades = [_trade(0, ev=0.005, actual_move_pct=0.5)] * 10  # predicho 0.5%, real 0.5% exacto
    result = compute_calibration(trades)
    assert result["calibration_score"] == pytest.approx(1.0)
    assert result["meets_target"] is True


def test_calibration_way_off_gives_low_or_negative_score():
    trades = [_trade(0, ev=0.005, actual_move_pct=-5.0)] * 10  # predijo +0.5%, pasó -5%
    result = compute_calibration(trades)
    assert result["calibration_score"] < CALIBRATION_TARGET
    assert result["meets_target"] is False


def test_calibration_undefined_when_predicted_is_zero():
    trades = [_trade(0, ev=0.0, actual_move_pct=1.0)]
    result = compute_calibration(trades)
    assert result["calibration_score"] is None


def test_calibration_empty_trades():
    result = compute_calibration([])
    assert result["n_trades"] == 0
    assert result["calibration_score"] is None


# ---------------------------------------------------------------------------
# compute_prediction_regression / top_n_trades
# ---------------------------------------------------------------------------


def test_prediction_regression_perfect_linear_relationship_gives_r_squared_one():
    trades = [_trade(0, ev=e, actual_move_pct=e * 100 * 2) for e in [0.001, 0.002, 0.003, 0.004, 0.005]]
    result = compute_prediction_regression(trades)
    assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)


def test_prediction_regression_no_relationship_gives_low_r_squared():
    trades = [_trade(0, ev=0.001, actual_move_pct=5), _trade(0, ev=0.002, actual_move_pct=-5),
              _trade(0, ev=0.003, actual_move_pct=5), _trade(0, ev=0.004, actual_move_pct=-5)]
    result = compute_prediction_regression(trades)
    assert result["r_squared"] < 0.5


def test_prediction_regression_scatter_has_one_point_per_trade():
    trades = [_trade(0, ev=0.001, actual_move_pct=1), _trade(0, ev=0.002, actual_move_pct=2)]
    result = compute_prediction_regression(trades)
    assert len(result["scatter"]) == 2


def test_top_n_trades_winners_and_losers():
    trades = [_trade(p) for p in [5, -3, 10, -8, 1, -1]]
    top_winners = top_n_trades(trades, n=2, winners=True)
    top_losers = top_n_trades(trades, n=2, winners=False)
    assert [t["pnl_pct"] for t in top_winners] == [10, 5]
    assert [t["pnl_pct"] for t in top_losers] == [-8, -3]


# ---------------------------------------------------------------------------
# compute_bias_report — contra Postgres real
# ---------------------------------------------------------------------------

import os

import pytest as _pytest


@_pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
def test_bias_report_against_real_postgres():
    from pipeline.backtest.portfolio_metrics import compute_bias_report
    from pipeline.db.connection import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE portfolio_trades, portfolio_equity_curve, car_results, backtest_runs, "
            "event_analyses, event_enrichment, events, prices, fama_french_factors, universe "
            "RESTART IDENTITY CASCADE"
        )
        # 4 tickers: 1 deslistado, 3 no.
        for i in range(4):
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date, is_delisted_flag) "
                "VALUES (%s,%s,'X','2024-01-01','2024-01-01',%s)",
                (str(i), f"T{i}", i == 0),
            )
        # 10 filas de precio, 2 con survivorship_warning.
        for i in range(10):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, close_raw, survivorship_warning) "
                "VALUES ('T0', %s, 100.0, %s)",
                (date(2024, 1, i + 1), i < 2),
            )
    conn.commit()

    report = compute_bias_report(conn)
    assert report["n_total_tickers"] == 4
    assert report["n_delisted"] == 1
    assert report["survivorship_bias_pct"] == pytest.approx(25.0)
    assert report["n_price_gaps"] == 2
    assert report["n_price_rows"] == 10
    assert report["data_gap_pct"] == pytest.approx(20.0)
    conn.close()
