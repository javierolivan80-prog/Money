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
    extraer_ticker_del_lote,
    nivel_de_tickers,
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


# --- Descarga por lotes -----------------------------------------------------
#
# Medido en producción (run 34943861450): de uno en uno, 150 tickers tardaron
# ~60 minutos. yf.download acepta una lista — de hecho ESA es la razón de que
# las columnas vengan en dos niveles. La librería siempre estuvo preparada para
# el modo por lotes; se estaba usando de una en una.


def _df_lote(tickers: list[str], vacios: tuple[str, ...] = ()) -> pd.DataFrame:
    """Lo que devuelve yf.download con una lista: columnas (campo, ticker).
    Los tickers en `vacios` vienen enteros a NaN, como los deslistados."""
    columnas, datos = [], {}
    for campo, valores in _VALORES.items():
        for t in tickers:
            columnas.append((campo, t))
            datos[(campo, t)] = [float("nan")] * len(valores) if t in vacios else valores
    df = pd.DataFrame(datos, index=_FECHAS)
    df.columns = pd.MultiIndex.from_tuples(columnas)
    return df


def test_saca_cada_ticker_del_lote_con_sus_propios_valores():
    lote = _df_lote(["AAPL", "MSFT"])
    for t in ("AAPL", "MSFT"):
        propio = extraer_ticker_del_lote(lote, t)
        assert list(propio.columns) == list(_VALORES)
        assert list(propio["Close"]) == _VALORES["Close"]


def test_el_sub_dataframe_del_lote_ya_sirve_para_float():
    """Lo que importa: lo que sale del lote tiene que poder guardarse sin más
    conversiones — es el mismo punto donde reventó float(row['Close'])."""
    propio = extraer_ticker_del_lote(_df_lote(["AAPL", "MSFT"]), "AAPL")
    for _, fila in propio.iterrows():
        for columna in COLUMNAS_REQUERIDAS:
            assert isinstance(float(fila[columna]), float)


def test_un_ticker_que_no_viene_en_el_lote_devuelve_none():
    """None es la señal de 'pídelo de uno en uno', no de 'está deslistado'."""
    assert extraer_ticker_del_lote(_df_lote(["AAPL"]), "MSFT") is None


def test_un_ticker_entero_a_nan_devuelve_none():
    """Un deslistado viene en las columnas pero sin un solo dato. No puede
    pasar como serie válida ni generar filas de precio."""
    assert extraer_ticker_del_lote(_df_lote(["AAPL", "NWSLL"], vacios=("NWSLL",)), "NWSLL") is None
    assert extraer_ticker_del_lote(_df_lote(["AAPL", "NWSLL"], vacios=("NWSLL",)), "AAPL") is not None


def test_lote_vacio_o_ausente_devuelve_none():
    """Si la descarga del lote falla entera, todos los tickers caen al camino
    de uno en uno en vez de darse por perdidos."""
    assert extraer_ticker_del_lote(None, "AAPL") is None
    assert extraer_ticker_del_lote(pd.DataFrame(), "AAPL") is None


def test_lote_que_vuelve_plano_se_acepta_igual():
    """Un lote de un solo ticker puede volver sin MultiIndex."""
    propio = extraer_ticker_del_lote(_df_plano(), "AAPL")
    assert list(propio["Close"]) == _VALORES["Close"]


def test_el_nivel_del_ticker_se_detecta_por_contenido_no_por_posicion():
    """El nivel de campos es el que trae 'Open'/'Close'; el otro es el de
    tickers. Detectarlo por contenido hace que invertir el orden de los
    niveles no rompa nada — el tipo de suposición sobre un formato ajeno que ya
    ha costado varios fallos en este proyecto."""
    normal = _df_lote(["AAPL", "MSFT"])           # (campo, ticker)
    assert nivel_de_tickers(normal.columns) == 1

    invertido = normal.copy()
    invertido.columns = pd.MultiIndex.from_tuples([(t, c) for c, t in normal.columns])
    assert nivel_de_tickers(invertido.columns) == 0
    assert list(extraer_ticker_del_lote(invertido, "AAPL")["Close"]) == _VALORES["Close"]


def test_no_mezcla_valores_entre_tickers_del_mismo_lote():
    """El fallo más caro que podría tener el modo por lotes: guardar los
    precios de una empresa bajo el ticker de otra. Pasaría desapercibido —los
    números son plausibles— y contaminaría todos los cálculos posteriores."""
    columnas, datos = [], {}
    for i, t in enumerate(["AAA", "BBB", "CCC"]):
        for campo, valores in _VALORES.items():
            columnas.append((campo, t))
            datos[(campo, t)] = [v + i * 100 for v in valores]
    lote = pd.DataFrame(datos, index=_FECHAS)
    lote.columns = pd.MultiIndex.from_tuples(columnas)

    for i, t in enumerate(["AAA", "BBB", "CCC"]):
        propio = extraer_ticker_del_lote(lote, t)
        assert list(propio["Close"]) == [v + i * 100 for v in _VALORES["Close"]]
