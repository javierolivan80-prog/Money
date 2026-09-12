"""test_edgar_parser.py — valida el parser de EDGAR contra fixtures offline.

Esto NO prueba que el scraper funcione contra el EDGAR real (el sandbox de
desarrollo no tiene salida de red hacia www.sec.gov — AUDIT_LEAN.md §1.5).
Prueba que la LÓGICA de parseo es correcta contra el formato documentado.
Es la parte de "verificación" que sí se puede hacer sin red, y es la que
hay que correr antes de gastar tiempo de backfill real.
"""
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from pipeline.ingest.edgar_scraper import (
    classify_event_classes,
    compute_d0_close_date,
    parse_daily_index,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_daily_index_filters_only_8k():
    raw = (FIXTURES / "sample_daily_index.idx").read_text()
    rows = parse_daily_index(raw)
    # 8-K/A (enmienda) y 10-K y Form 4 deben quedar excluidos: solo 8-K exacto.
    forms_found = {r["form_type"] for r in rows}
    assert forms_found == {"8-K"}
    assert len(rows) == 3  # ACME, BETA, EPSILON


def test_parse_daily_index_extracts_correct_fields():
    raw = (FIXTURES / "sample_daily_index.idx").read_text()
    rows = parse_daily_index(raw)
    acme = next(r for r in rows if "ACME" in r["company_name"])
    assert acme["cik"] == "1234567"
    assert acme["date_filed"] == "2024-03-15"
    assert acme["file_name"] == "edgar/data/1234567/0001234567-24-000123.txt"


def test_item_extraction_numeric_format():
    """Cabecera con el Item impreso como número ('2.02')."""
    text = (FIXTURES / "sample_8k_header_numeric.txt").read_text()
    items = re.findall(r"ITEM INFORMATION:\s*(.+)", text)
    numeric_items = [m.group(1) for line in items if (m := re.match(r"^(\d\.\d\d)\b", line.strip()))]
    # La cabecera trae 2.02 (earnings) y 9.01 (exhibits, no es clase de evento).
    # El filtrado de 9.01 ocurre después, en classify_event_classes — no aquí.
    assert numeric_items == ["2.02", "9.01"]


def test_item_extraction_titled_format():
    """Cabecera con el Item impreso como título en texto libre.

    Esta es la rama de fallback que existe precisamente porque no se pudo
    verificar en vivo cuál de los dos formatos usa EDGAR realmente.
    """
    from pipeline.ingest.edgar_scraper import ITEM_TITLE_TO_NUMBER

    text = (FIXTURES / "sample_8k_header_titled.txt").read_text()
    lines = re.findall(r"ITEM INFORMATION:\s*(.+)", text)
    found = []
    for line in lines:
        key = line.strip().lower().rstrip(".")
        for title, number in ITEM_TITLE_TO_NUMBER.items():
            if title in key:
                found.append(number)
    assert found == ["2.02"]


def test_classify_event_classes_dedupes_and_ignores_irrelevant_items():
    # 9.01 (exhibits) no es una clase de evento — no debe aparecer ni generar ruido.
    assert classify_event_classes(["2.02", "9.01"]) == ["8K_2.02_EARNINGS"]


def test_classify_event_classes_multiple_relevant_items():
    result = classify_event_classes(["1.01", "5.02"])
    assert result == ["8K_1.01_MATERIAL_AGMT", "8K_5.02_MGMT_CHANGE"]


def test_classify_event_classes_unknown_item_only():
    assert classify_event_classes(["7.01"]) == []  # Reg FD, fuera de alcance del POC


@pytest.mark.parametrize(
    "filed_at,expected",
    [
        # Filed a las 09:00 ET un martes -> D0 es ese mismo día
        (datetime(2024, 3, 12, 9, 0), date(2024, 3, 12)),
        # Filed a las 17:00 ET (after-hours) un martes -> D0 pasa al miércoles
        (datetime(2024, 3, 12, 17, 0), date(2024, 3, 13)),
        # Filed after-hours un viernes -> D0 salta el fin de semana, cae en lunes
        (datetime(2024, 3, 15, 17, 0), date(2024, 3, 18)),
        # Filed temprano un sábado (no debería pasar en la práctica, pero si el
        # dato viene sucio, D0 debe seguir cayendo en día hábil)
        (datetime(2024, 3, 16, 9, 0), date(2024, 3, 18)),
    ],
)
def test_compute_d0_close_date_never_lands_on_weekend_or_before_filing(filed_at, expected):
    result = compute_d0_close_date(filed_at)
    assert result == expected
    assert result.weekday() < 5
    assert result >= filed_at.date()  # D0 nunca es anterior al día de presentación
