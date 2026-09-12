"""test_fama_french.py — valida el parser de Ken French contra un fixture offline.

No se pudo verificar contra el fichero real (mba.tuck.dartmouth.edu bloqueado
en este sandbox). Fixture con la misma forma documentada del CSV real:
cabecera de texto libre, tabla de datos, footer de copyright.
"""
from datetime import date
from pathlib import Path

import pytest

from pipeline.ingest.fama_french import parse_ff3_csv

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
