"""test_yfinance_backfill.py — forma de lo que devuelve yfinance.

Estos tests no prueban la descarga (no hay salida hacia Yahoo desde el entorno
de desarrollo), sino el ADAPTADOR entre lo que devuelve la librería y lo que
espera el resto del pipeline. Es justo la costura donde falló en producción:
yfinance entrega las columnas en dos niveles (campo, ticker) incluso pidiendo
un solo ticker, así que row['Close'] era una Series de un elemento y float()
reventaba a mitad del backfill con un TypeError que no decía nada del problema
real.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipeline.ingest.yfinance_backfill import (
    COLUMNAS_REQUERIDAS,
    _validar_columnas,
    aplanar_columnas,
)

_FECHAS = pd.to_datetime(["2026-09-09", "2026-09-10"])
_VALORES = {
    "Open": [10.0, 11.0],
    "High": [10.5, 11.5],
    "Low": [9.5, 10.5],
    "Close": [10.2, 11.2],
    "Adj Close": [10.1, 11.1],
    "Volume": [1000, 2000],
}


def _df_plano() -> pd.DataFrame:
    return pd.DataFrame(_VALORES, index=_FECHAS)


def _df_multiindex(ticker: str = "AAPL", ticker_al_final: bool = True) -> pd.DataFrame:
    """Lo que devuelve yfinance de verdad: columnas (campo, ticker)."""
    df = _df_plano()
    niveles = [(c, ticker) for c in df.columns] if ticker_al_final else [(ticker, c) for c in df.columns]
    df.columns = pd.MultiIndex.from_tuples(niveles)
    return df


def test_una_columna_multiindex_da_una_series_no_un_numero():
    """EL fallo de producción, reproducido: sin aplanar, float(row['Close'])
    recibe una Series. Este test fija POR QUÉ existe aplanar_columnas."""
    df = _df_multiindex()
    fila = next(df.iterrows())[1]
    assert isinstance(fila["Close"], pd.Series)
    with pytest.raises(TypeError):
        float(fila["Close"])


def test_aplanar_deja_valores_convertibles_a_float():
    df = aplanar_columnas(_df_multiindex(), "AAPL")
    for _, fila in df.iterrows():
        for columna in COLUMNAS_REQUERIDAS:
            assert isinstance(float(fila[columna]), float)


def test_aplanar_encuentra_el_ticker_aunque_no_esté_en_el_último_nivel():
    """No se asume la posición del nivel del ticker: se busca cuál es. Si
    yfinance invierte el orden, el aplanado tiene que seguir funcionando."""
    df = aplanar_columnas(_df_multiindex(ticker_al_final=False), "AAPL")
    assert list(df.columns) == list(_VALORES)


def test_aplanar_no_toca_un_dataframe_que_ya_viene_plano():
    plano = _df_plano()
    assert aplanar_columnas(plano, "AAPL").equals(plano)


def test_aplanar_es_idempotente():
    """_store_with_gap_detection lo llama otra vez por si acaso; llamarlo dos
    veces no puede estropear nada."""
    una_vez = aplanar_columnas(_df_multiindex(), "AAPL")
    dos_veces = aplanar_columnas(una_vez, "AAPL")
    assert una_vez.equals(dos_veces)


def test_aplanar_conserva_los_valores_exactos():
    df = aplanar_columnas(_df_multiindex(), "AAPL")
    assert list(df["Close"]) == _VALORES["Close"]
    assert list(df.index) == list(_FECHAS)


def test_validar_columnas_acepta_el_dataframe_bueno():
    _validar_columnas(_df_plano(), "AAPL")  # no debe lanzar


def test_validar_columnas_dice_cuál_falta_y_qué_llegó():
    """El error tiene que nombrar lo que falta Y volcar lo recibido: adivinar
    el formato en vez de mirarlo es lo que costó tres intentos en el parser
    de EDGAR."""
    df = _df_plano().drop(columns=["Adj Close"])
    with pytest.raises(ValueError, match="Adj Close") as exc:
        _validar_columnas(df, "AAPL")
    assert "Columnas recibidas" in str(exc.value)


def test_validar_columnas_caza_una_columna_duplicada():
    """Una columna repetida devuelve otra vez una Series en row['Close'] — el
    mismo TypeError críptico por otra puerta. Mejor que falle aquí, diciendo
    qué pasa."""
    df = _df_plano()
    df = pd.concat([df, df[["Close"]]], axis=1)
    with pytest.raises(ValueError, match="duplicadas"):
        _validar_columnas(df, "AAPL")
