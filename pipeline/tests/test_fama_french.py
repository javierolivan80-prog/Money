"""test_fama_french.py — valida el parser de Ken French contra un fixture offline.

No se pudo verificar contra el fichero real (mba.tuck.dartmouth.edu bloqueado
en este sandbox). Fixture con la misma forma documentada del CSV real:
cabecera de texto libre, tabla de datos, footer de copyright.
"""
from datetime import date
from pathlib import Path

import pytest

from pipeline.ingest.fama_french import _default_min_date, parse_ff3_csv

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_ff3_csv_skips_header_and_footer_text():
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw)
    assert len(df) == 3  # solo las 3 filas de datos, no cabecera ni footer


def test_parse_ff3_csv_converts_percent_points_to_fractional_returns():
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw)
    first = df.iloc[0]
    assert first["trade_date"] == date(2021, 1, 4)
    assert first["mkt_rf"] == pytest.approx(-0.0036)  # -0.36 puntos porcentuales -> -0.0036 fraccional
    assert first["smb"] == pytest.approx(0.012)
    assert first["hml"] == pytest.approx(0.036)
    assert first["rf"] == pytest.approx(0.0)


def test_parse_ff3_csv_without_min_date_returns_all_rows():
    """Default (min_date=None) no filtra — mismo comportamiento de antes de
    añadir el filtro, para no romper ningún caller existente."""
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw)
    assert len(df) == 3


def test_parse_ff3_csv_min_date_filters_earlier_rows():
    """Regresión: sin filtrar por fecha, fetch_ff3_daily() descargaba el
    histórico completo de Ken French desde 1926 (~25.000 filas) y
    store_factors() las insertaba una a una contra Neon — el paso del
    pipeline nocturno real se quedó colgado más de 2 horas sin fallar. El
    filtro reduce esto a solo lo que el proyecto necesita."""
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw, min_date=date(2021, 1, 5))
    assert len(df) == 2  # descarta la fila del 2021-01-04, quedan 2 de las 3
    assert all(d >= date(2021, 1, 5) for d in df["trade_date"])


def test_parse_ff3_csv_min_date_after_all_rows_returns_empty():
    raw = (FIXTURES / "sample_ff3_daily.csv").read_text()
    df = parse_ff3_csv(raw, min_date=date(2099, 1, 1))
    assert len(df) == 0


def test_default_min_date_is_before_backtest_start():
    """_default_min_date() debe quedar ANTES de BACKTEST_START (necesita
    colchón para la ventana de estimación de -250 días de mercado), nunca
    después — si no, event_enrichment.py no tendría suficiente historial de
    factores para ajustar el modelo en los primeros eventos del backtest."""
    from pipeline import config

    backtest_start = date.fromisoformat(config.BACKTEST_START)
    assert _default_min_date() < backtest_start
    assert (backtest_start - _default_min_date()).days >= 250
