"""factor_model.py — ajuste de la regresión de factores Fama-French 3, compartido.

Extraído de backtest/backtester.py:compute_car() en la Fase 2, porque
analyze/enrichment.py también necesita el mismo ajuste (para beta_vs_spy,
ff_size_exposure, ff_value_exposure de la Etapa 1) y duplicar la regresión en
dos sitios es exactamente el tipo de cosa que diverge silenciosamente con el
tiempo (un cambio en la ventana de estimación en un sitio y no en el otro).

Una sola fuente de verdad: compute_car (event study, Fase 1) y enrichment.py
(features de la Etapa 1, Fase 2) llaman a la misma función.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd
import statsmodels.api as sm

logger = logging.getLogger(__name__)

MIN_ESTIMATION_DAYS = 60  # umbral conservador — por debajo de esto, la regresión es puro ruido


@dataclass
class FactorModelFit:
    model: object  # RegressionResultsWrapper de statsmodels — sin tipar explícito para no atar
    # este módulo a una versión interna de statsmodels en la firma pública.
    beta_mkt: float
    beta_smb: float
    beta_hml: float
    n_estimation_days: int


def fit_factor_model(
    merged: pd.DataFrame,
    d0_close_date: date,
    estimation_window: tuple[int, int] = (-250, -30),
) -> FactorModelFit | None:
    """Ajusta (ret - rf) ~ mkt_rf + smb + hml por OLS en la ventana de
    estimación relativa a d0_close_date.

    merged: DataFrame indexado por fecha con columnas 'ret', 'mkt_rf', 'smb',
        'hml', 'rf' ya unidas (el caller decide cómo construir el join).

    Devuelve None si hay menos de MIN_ESTIMATION_DAYS días de datos en la
    ventana — un None debe tratarse como "no evaluable", nunca como
    coeficientes de valor 0 (eso sería fabricar una beta de mercado neutral
    que no se sabe si es cierta).
    """
    est_start = pd.Timestamp(d0_close_date) + pd.Timedelta(days=estimation_window[0])
    est_end = pd.Timestamp(d0_close_date) + pd.Timedelta(days=estimation_window[1])
    est_data = merged[(merged.index >= est_start) & (merged.index <= est_end)]

    if len(est_data) < MIN_ESTIMATION_DAYS:
        logger.warning(
            "Ventana de estimación insuficiente (%d días, mínimo %d) — no evaluable",
            len(est_data),
            MIN_ESTIMATION_DAYS,
        )
        return None

    y = est_data["ret"] - est_data["rf"]
    X = sm.add_constant(est_data[["mkt_rf", "smb", "hml"]])
    model = sm.OLS(y, X).fit()

    return FactorModelFit(
        model=model,
        beta_mkt=float(model.params.get("mkt_rf", float("nan"))),
        beta_smb=float(model.params.get("smb", float("nan"))),
        beta_hml=float(model.params.get("hml", float("nan"))),
        n_estimation_days=len(est_data),
    )
