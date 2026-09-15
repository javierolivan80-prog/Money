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


def test_parse_daily_index_contra_el_formato_real_de_edgar():
    """EL formato de verdad, capturado del propio servidor de la SEC el
    2026-09-10 (run 34886811117, volcado en el mensaje del ValueError).

    Este fichero es la razón de ser del resto de tests de este bloque: los dos
    parsers anteriores se escribieron contra fixtures INVENTADOS porque desde
    el entorno de desarrollo no hay salida hacia sec.gov, y ambos fallaron en
    producción devolviendo 0 eventos. Lo que ninguna suposición acertó:

      - La fecha viene COMPACTA ('20260910'), no en ISO ('2026-09-10'). Ese
        detalle, y solo ese, tumbaba el parseo entero.
      - El separador es una tira continua de guiones, no tramos por columna.
      - Entre la cabecera y la tabla hay líneas con espacios en blanco.
      - Los nombres de empresa llevan comas y puntos ('Glow Holdings, Inc.').
      - El fichero trae TODOS los tipos de formulario (1-A, 1-A-W, 10-K...),
        no solo 8-K.
    """
    raw = (FIXTURES / "sample_daily_index_real.idx").read_text()
    rows = parse_daily_index(raw)

    assert {r["form_type"] for r in rows} == {"8-K"}
    assert len(rows) == 3  # ACME, Beta, EPSILON — ni el 8-K/A, ni el 10-K, ni los 1-A

    acme = next(r for r in rows if "ACME" in r["company_name"])
    assert acme["cik"] == "1234567"
    assert acme["date_filed"] == "2026-09-10"  # normalizada a ISO desde 20260910
    assert acme["file_name"] == "edgar/data/1234567/0001234567-26-000123.txt"


def test_parse_daily_index_normaliza_la_fecha_compacta_a_iso():
    """El resto del pipeline hace strptime('%Y-%m-%d') sobre date_filed
    (ver scrape_day), así que la fecha compacta del fichero real tiene que
    salir ya convertida o reventaría un paso más adelante."""
    from datetime import datetime

    raw = (FIXTURES / "sample_daily_index_real.idx").read_text()
    for row in parse_daily_index(raw):
        # No debe lanzar: es exactamente lo que hace scrape_day.
        datetime.strptime(row["date_filed"], "%Y-%m-%d")


def test_parse_daily_index_nombres_con_coma_y_punto():
    """'Beta Biosciences, Inc.' tiene coma y punto; el corte entre columnas
    son 2+ espacios, no la puntuación."""
    raw = (FIXTURES / "sample_daily_index_real.idx").read_text()
    beta = next(r for r in parse_daily_index(raw) if r["cik"] == "9876543")
    assert beta["company_name"] == "Beta Biosciences, Inc."


def test_parse_daily_index_con_separador_de_guiones_continuo():
    """REGRESIÓN (bug real, 2026-09-14): el parser derivaba el corte de cada
    columna de la línea de guiones, asumiendo tramos separados por columna
    ('---- ---- ----'). Con una TIRA CONTINUA de guiones esa lógica colapsaba
    a una sola columna, form_type pasaba a ser la línea entera, y el filtro
    `!= "8-K"` descartaba todas las filas.

    El síntoma en producción no fue un error sino algo peor: HTTP 200, cero
    excepciones, '0 formularios 8-K encontrados' todos los días y el pipeline
    completo en verde sobre una base de datos vacía. El fixture original se
    había escrito con el formato segmentado, así que los tests confirmaban la
    suposición equivocada en lugar de contrastarla.
    """
    raw = (FIXTURES / "sample_daily_index_continuous_sep.idx").read_text()
    rows = parse_daily_index(raw)

    assert {r["form_type"] for r in rows} == {"8-K"}
    assert len(rows) == 3  # ACME, BETA, EPSILON — no el 8-K/A ni el 10-K
    acme = next(r for r in rows if "ACME" in r["company_name"])
    assert acme["cik"] == "1234567"
    assert acme["date_filed"] == "2026-08-14"
    assert acme["file_name"] == "edgar/data/1234567/0001234567-26-000123.txt"


def test_parse_daily_index_da_el_mismo_resultado_con_ambos_separadores():
    """El invariante que importa: el MISMO contenido parseado igual, se dibuje
    la línea separadora en tramos por columna o como una tira continua.

    Se genera la variante continua a partir del fixture segmentado en vez de
    comparar dos ficheros distintos — así el test comprueba el efecto del
    separador y nada más."""
    segmentado = (FIXTURES / "sample_daily_index.idx").read_text()
    lineas = segmentado.splitlines()
    idx_sep = next(i for i, l in enumerate(lineas) if set(l.strip()) <= {"-", " "} and "-" in l)
    lineas[idx_sep] = "-" * len(lineas[idx_sep])
    continuo = "\n".join(lineas)

    assert parse_daily_index(segmentado) == parse_daily_index(continuo)


def test_parse_daily_index_falla_ruidosamente_si_no_reconoce_ninguna_fila():
    """Cero filas reconocibles tiene que ROMPER, no devolver []. Un
    daily-index de un día hábil siempre trae filings; devolver vacío en
    silencio fue lo que permitió que el pipeline corriera semanas en verde
    sin ingestar nada."""
    basura = "Description: algo\nOtra cabecera\n\ntexto que no es una tabla\n"
    with pytest.raises(ValueError, match="Formato de daily-index inesperado"):
        parse_daily_index(basura)


def test_parse_daily_index_no_confunde_nombres_con_espacios():
    """Los nombres de empresa llevan espacios simples; el corte entre columnas
    son 2+ espacios. 'EPSILON ENERGY PARTNERS LP' debe salir entero."""
    raw = (FIXTURES / "sample_daily_index_continuous_sep.idx").read_text()
    rows = parse_daily_index(raw)
    epsilon = next(r for r in rows if r["cik"] == "3334445")
    assert epsilon["company_name"] == "EPSILON ENERGY PARTNERS LP"


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
