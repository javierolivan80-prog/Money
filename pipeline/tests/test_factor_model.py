"""test_factor_model.py — el ajuste de factores compartido entre backtester.py
(Fase 1) y analyze/enrichment.py (Fase 2). Ver docstring del módulo para por
qué se extrajo: evitar que la ventana de estimación diverja entre los dos
sitios que la usan."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline.backtest.factor_model import MIN_ESTIMATION_DAYS, fit_factor_model


def _synthetic_panel(n_days: int, beta_mkt: float, beta_smb: float, beta_hml: float, seed: int = 1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2021-01-04", periods=n_days, freq="B")
    factors = pd.DataFrame(
        {
            "mkt_rf": rng.normal(0.0003, 0.01, n_days),
            "smb": rng.normal(0.0, 0.005, n_days),
            "hml": rng.normal(0.0, 0.005, n_days),
            "rf": np.full(n_days, 0.00005),
        },
        index=dates,
    )
    noise = rng.normal(0, 0.003, n_days)  # ruido idiosincrático pequeño para que la beta recuperada sea nítida
    ret = factors["rf"] + beta_mkt * factors["mkt_rf"] + beta_smb * factors["smb"] + beta_hml * factors["hml"] + noise
    merged = factors.copy()
    merged["ret"] = ret
    return merged


def test_fit_factor_model_recovers_known_betas():
    merged = _synthetic_panel(320, beta_mkt=1.3, beta_smb=0.4, beta_hml=-0.6)
    d0 = merged.index[280].date()
    fit = fit_factor_model(merged, d0)

    assert fit is not None
    assert fit.beta_mkt == pytest.approx(1.3, abs=0.15)
    assert fit.beta_smb == pytest.approx(0.4, abs=0.2)
    assert fit.beta_hml == pytest.approx(-0.6, abs=0.2)
    assert fit.n_estimation_days >= MIN_ESTIMATION_DAYS


def test_fit_factor_model_returns_none_below_minimum_days():
    merged = _synthetic_panel(20, beta_mkt=1.0, beta_smb=0.0, beta_hml=0.0)
    d0 = merged.index[-1].date()
    fit = fit_factor_model(merged, d0)
    assert fit is None


def test_fit_factor_model_never_returns_zero_betas_as_fallback():
    """Un caso límite pero evaluable no debe devolver betas fabricadas de 0 —
    si el ajuste es posible, las betas deben venir de la regresión real, no de
    un valor por defecto que finja neutralidad de mercado."""
    merged = _synthetic_panel(100, beta_mkt=2.0, beta_smb=0.0, beta_hml=0.0)
    d0 = merged.index[-1].date()
    fit = fit_factor_model(merged, d0, estimation_window=(-90, -1))
    assert fit is not None
    assert fit.beta_mkt != 0.0
