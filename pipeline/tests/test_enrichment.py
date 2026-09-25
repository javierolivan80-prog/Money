"""test_enrichment.py — Etapa 1.

compute_enrichment() con paneles sintéticos (sin red, sin BD) + una
verificación end-to-end de fetch_and_compute_enrichment() contra Postgres
real, sembrando SPY/sector ETF/^VIX como tickers normales en `prices` (que es
exactamente como vive el diseño: no hay tabla especial para índices).
"""
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline.analyze.enrichment import compute_enrichment, sic_to_sector_etf

# ---------------------------------------------------------------------------
# sic_to_sector_etf — lógica pura
# ---------------------------------------------------------------------------


def test_sic_to_sector_etf_known_prefix():
    assert sic_to_sector_etf("2836") == "XLV"  # biológicos -> healthcare
    assert sic_to_sector_etf("7372") == "XLK"  # software -> tech


def test_sic_to_sector_etf_unknown_or_missing_defaults_safely():
    assert sic_to_sector_etf(None) == "SPY"
    assert sic_to_sector_etf("") == "SPY"
    assert sic_to_sector_etf("9999") == "SPY"  # SIC no mapeado, no debe reventar


# ---------------------------------------------------------------------------
# compute_enrichment — paneles sintéticos
# ---------------------------------------------------------------------------


def _panel(n_days: int, base_price: float, daily_ret: float, seed: int, volume: int = 100_000, vol_jump_at: int | None = None):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    rets = rng.normal(daily_ret, 0.01, n_days)
    prices = base_price * np.exp(np.cumsum(rets))
    volumes = np.full(n_days, volume)
    if vol_jump_at is not None:
        volumes[vol_jump_at] = volume * 5
    df = pd.DataFrame(
        {
            "close_raw": prices,
            "high_raw": prices * 1.01,
            "low_raw": prices * 0.99,
            "adj_factor": 1.0,
            "volume": volumes,
            "survivorship_warning": False,
        },
        index=dates,
    )
    return df


def _factors(n_days: int, seed: int = 99):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    return pd.DataFrame(
        {
            "mkt_rf": rng.normal(0.0003, 0.01, n_days),
            "smb": rng.normal(0.0, 0.005, n_days),
            "hml": rng.normal(0.0, 0.005, n_days),
            "rf": np.full(n_days, 0.00005),
        },
        index=dates,
    )


def test_price_d0_and_lookbacks_are_extracted_correctly():
    ticker = _panel(320, 100.0, 0.0005, seed=1)
    spy = _panel(320, 400.0, 0.0004, seed=2)
    sector = _panel(320, 50.0, 0.0004, seed=3)
    vix = _panel(320, 18.0, 0.0, seed=4)
    factors = _factors(320)
    d0 = ticker.index[300].date()

    result = compute_enrichment(ticker, spy, sector, vix, factors, d0, "XLK")

    assert result.price_d0 == pytest.approx(ticker.loc[pd.Timestamp(d0), "close_raw"])
    assert result.price_d_minus_5 is not None
    assert result.price_d_minus_20 is not None
    assert result.n_estimation_days > 0


def test_volume_ratio_detects_spike_at_d0():
    ticker = _panel(320, 100.0, 0.0, seed=5, volume=100_000)
    d0_idx = 300
    ticker.iloc[d0_idx, ticker.columns.get_loc("volume")] = 800_000  # spike de 8x justo en D0
    spy = _panel(320, 400.0, 0.0, seed=6)
    sector = _panel(320, 50.0, 0.0, seed=7)
    vix = _panel(320, 18.0, 0.0, seed=8)
    factors = _factors(320)
    d0 = ticker.index[d0_idx].date()

    result = compute_enrichment(ticker, spy, sector, vix, factors, d0, "XLK")
    assert result.volume_ratio is not None
    assert result.volume_ratio > 5.0  # el spike debe reflejarse claramente


def test_pre_event_drift_reflects_d5_to_d1_move():
    ticker = _panel(320, 100.0, 0.0, seed=9)
    d0_idx = 300
    # Fuerza un salto claro entre D-5 y D-1 para que el drift sea inequívoco.
    ticker.iloc[d0_idx - 5 : d0_idx, ticker.columns.get_loc("close_raw")] = np.linspace(100, 110, 5)
    spy = _panel(320, 400.0, 0.0, seed=10)
    sector = _panel(320, 50.0, 0.0, seed=11)
    vix = _panel(320, 18.0, 0.0, seed=12)
    factors = _factors(320)
    d0 = ticker.index[d0_idx].date()

    result = compute_enrichment(ticker, spy, sector, vix, factors, d0, "XLK")
    assert result.pre_event_drift_pct is not None
    assert result.pre_event_drift_pct > 0  # el precio subió de D-5 a D-1


def test_high_low_range_pct_computed_at_d0():
    ticker = _panel(320, 100.0, 0.0, seed=13)
    d0_idx = 300
    d0 = ticker.index[d0_idx].date()
    ticker.loc[pd.Timestamp(d0), "high_raw"] = 110.0
    ticker.loc[pd.Timestamp(d0), "low_raw"] = 90.0
    ticker.loc[pd.Timestamp(d0), "close_raw"] = 100.0
    spy = _panel(320, 400.0, 0.0, seed=14)
    sector = _panel(320, 50.0, 0.0, seed=15)
    vix = _panel(320, 18.0, 0.0, seed=16)
    factors = _factors(320)

    result = compute_enrichment(ticker, spy, sector, vix, factors, d0, "XLK")
    assert result.high_low_range_pct == pytest.approx(20.0)  # (110-90)/100 * 100


def test_survivorship_warning_propagates_when_present_near_d0():
    ticker = _panel(320, 100.0, 0.0, seed=17)
    d0_idx = 300
    d0 = ticker.index[d0_idx].date()
    ticker.loc[pd.Timestamp(d0), "survivorship_warning"] = True
    spy = _panel(320, 400.0, 0.0, seed=18)
    sector = _panel(320, 50.0, 0.0, seed=19)
    vix = _panel(320, 18.0, 0.0, seed=20)
    factors = _factors(320)

    result = compute_enrichment(ticker, spy, sector, vix, factors, d0, "XLK")
    assert result.had_survivorship_warning is True


def test_missing_sector_or_spy_data_degrades_sector_mood_to_none_not_crash():
    ticker = _panel(320, 100.0, 0.0, seed=21)
    d0 = ticker.index[300].date()
    factors = _factors(320)
    result = compute_enrichment(ticker, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), factors, d0, "SPY")
    assert result.sector_mood is None
    assert result.vix_d0 is None
    assert result.price_d0 is not None  # el resto del enrichment no debe verse afectado


def test_insufficient_history_gives_none_betas_not_zero():
    ticker = _panel(20, 100.0, 0.0, seed=22)  # muy por debajo del mínimo de estimación
    spy = _panel(20, 400.0, 0.0, seed=23)
    sector = _panel(20, 50.0, 0.0, seed=24)
    vix = _panel(20, 18.0, 0.0, seed=25)
    factors = _factors(20)
    d0 = ticker.index[-1].date()

    result = compute_enrichment(ticker, spy, sector, vix, factors, d0, "XLK")
    assert result.beta_vs_spy is None
    assert result.ff_size_exposure is None
    assert result.n_estimation_days == 0


# ---------------------------------------------------------------------------
# fetch_and_compute_enrichment — contra Postgres real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
def test_fetch_and_compute_enrichment_end_to_end_against_real_postgres():
    from pipeline.analyze.enrichment import fetch_and_compute_enrichment
    from pipeline.db.connection import get_connection, init_schema

    conn = get_connection()
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
            "fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    conn.commit()

    rng = np.random.default_rng(42)
    dates = pd.date_range("2021-01-04", periods=320, freq="B")

    def seed_ticker(ticker: str, base: float):
        with conn.cursor() as cur:
            for i, d in enumerate(dates):
                price = base * (1 + 0.0003 * i + rng.normal(0, 0.01))
                cur.execute(
                    "INSERT INTO prices (ticker, trade_date, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                    "VALUES (%s,%s,%s,%s,%s,1.0,100000,FALSE) ON CONFLICT (ticker, trade_date) DO NOTHING",
                    (ticker, d.date(), price, price * 1.01, price * 0.99),
                )
        conn.commit()

    seed_ticker("TESTCO", 50.0)
    seed_ticker("SPY", 400.0)
    seed_ticker("XLV", 100.0)
    seed_ticker("^VIX", 18.0)

    # Factores con variación real día a día (no constantes): una matriz de
    # diseño con columnas de varianza cero es rank-deficient para OLS —
    # se encontró este warning al ejecutar el test la primera vez.
    with conn.cursor() as cur:
        for d in dates:
            cur.execute(
                "INSERT INTO fama_french_factors (trade_date, mkt_rf, smb, hml, rf) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (trade_date) DO NOTHING",
                (d.date(), float(rng.normal(0.0003, 0.008)), float(rng.normal(0.0001, 0.004)), float(rng.normal(-0.0001, 0.004)), 0.00005),
            )
    conn.commit()

    event = {"ticker": "TESTCO", "d0_close_date": dates[280].date(), "sic_code": "2836"}
    result = fetch_and_compute_enrichment(conn, event)

    assert result.price_d0 is not None
    assert result.sector_etf_ticker == "XLV"
    assert result.beta_vs_spy is not None
    assert result.vix_d0 == pytest.approx(18.0, abs=2.0)
    conn.close()


def test_compute_enrichment_sin_factores_ni_precios_no_revienta():
    """Antes: TypeError/KeyError después de pagar la IA. Ahora: todo None."""
    import pandas as pd

    from pipeline.analyze.enrichment import compute_enrichment

    vacio = pd.DataFrame()
    r = compute_enrichment(vacio, vacio, vacio, vacio, vacio, date(2024, 3, 15), "SPY")
    assert r.price_d0 is None
    assert r.beta_vs_spy is None
    assert r.n_estimation_days == 0


def test_benchmark_tickers_incluye_spy_vix_y_sectores():
    from pipeline.analyze.enrichment import BENCHMARK_TICKERS

    assert {"SPY", "^VIX", "XLK", "XLV", "XLF"} <= set(BENCHMARK_TICKERS)
