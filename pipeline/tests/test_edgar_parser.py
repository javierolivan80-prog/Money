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
    archive_url,
    classify_event_classes,
    compute_d0_close_date,
    daily_index_url,
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


def test_archive_url_mete_el_documento_bajo_archives():
    """BUG REAL (2026-09-15): las rutas del daily-index son relativas al árbol
    de archivo, que cuelga de /Archives/. Pegarlas al dominio a secas daba
    https://www.sec.gov/edgar/data/... — 404 en TODOS los filings, ni uno se
    llegó a descargar."""
    url = archive_url("edgar/data/1234567/0001234567-26-000123.txt")
    assert url == "https://www.sec.gov/Archives/edgar/data/1234567/0001234567-26-000123.txt"


def test_archive_url_no_duplica_archives_si_ya_viene_en_la_ruta():
    ya_absoluta = "Archives/edgar/data/1234567/0001234567-26-000123.txt"
    assert archive_url(ya_absoluta).count("Archives") == 1


def test_archive_url_tolera_la_barra_inicial():
    assert archive_url("/edgar/data/1/x.txt") == archive_url("edgar/data/1/x.txt")


def test_el_indice_y_los_documentos_cuelgan_del_mismo_arbol():
    """El invariante que se rompió: daily_index_url SÍ ponía /Archives/ y
    archive_url no. Las dos funciones apuntan al mismo árbol de EDGAR, así que
    o las dos lo llevan o ninguna — que discreparan fue justo el fallo."""
    indice = daily_index_url(date(2026, 9, 10))
    documento = archive_url("edgar/data/1234567/0001234567-26-000123.txt")
    assert indice.startswith("https://www.sec.gov/Archives/")
    assert documento.startswith("https://www.sec.gov/Archives/")


def test_source_url_guardado_apunta_a_un_documento_descargable():
    """source_url no es decorativo: se guarda en la base de datos, filing_text.py
    lo usa después para bajar el texto del filing, y la interfaz lo enlaza. Si
    sale mal formado, el fallo aparece tres pasos más adelante."""
    raw = (FIXTURES / "sample_daily_index_real.idx").read_text()
    for row in parse_daily_index(raw):
        url = archive_url(row["file_name"])
        assert url.startswith("https://www.sec.gov/Archives/edgar/data/")
        assert url.endswith(".txt")


def test_item_extraction_numeric_format():
    """Cabecera con el Item impreso como número ('2.02')."""
    text = (FIXTURES / "sample_8k_header_numeric.txt").read_text()
    items = re.findall(r"ITEM INFORMATION:\s*(.+)", text)
    numeric_items = [m.group(1) for line in items if (m := re.match(r"^(\d\.\d\d)\b", line.strip()))]
    # La cabecera trae 2.02 (earnings) y 9.01 (exhibits, no es clase de evento).
    # El filtrado de 9.01 ocurre después, en classify_event_classes — no aquí.
    assert numeric_items == ["2.02", "9.01"]


def test_item_extraction_titled_format():
    """Cabecera con el Item impreso como título en texto libre. Este es el
    formato que EDGAR usa DE VERDAD (confirmado sobre la cabecera real), no el
    numérico.

    Devuelve 2.02 y 9.01, lo mismo que test_item_extraction_numeric_format
    sobre la misma cabecera en formato numérico: los dos formatos tienen que
    dar el mismo resultado. Antes salía solo 2.02 porque 9.01 no estaba en el
    mapa y se perdía por el camino; descartar 9.01 es trabajo de
    classify_event_classes, no del extractor.
    """
    text = (FIXTURES / "sample_8k_header_titled.txt").read_text()
    assert _items_de_cabecera(text) == ["2.02", "9.01"]


def test_los_dos_formatos_de_cabecera_dan_el_mismo_resultado():
    """El invariante: dé EDGAR el número o el título, el pipeline tiene que ver
    los mismos Items."""
    numerico = (FIXTURES / "sample_8k_header_numeric.txt").read_text()
    titulado = (FIXTURES / "sample_8k_header_titled.txt").read_text()
    assert _items_de_cabecera(numerico) == _items_de_cabecera(titulado)


def _items_de_cabecera(texto: str) -> list[str]:
    """Mismo extractor que fetch_filing_item_codes, sobre un texto dado."""
    from pipeline.ingest.edgar_scraper import ITEM_TITLE_TO_NUMBER

    encontrados = []
    for linea in re.findall(r"ITEM INFORMATION:\s*(.+)", texto):
        linea = linea.strip()
        numerico = re.match(r"^(\d\.\d\d)\b", linea)
        if numerico:
            encontrados.append(numerico.group(1))
            continue
        clave = linea.lower().rstrip(".")
        coincidencias = [(t, n) for t, n in ITEM_TITLE_TO_NUMBER.items() if t in clave]
        if coincidencias:
            encontrados.append(max(coincidencias, key=lambda par: len(par[0]))[1])
    return encontrados


@pytest.mark.parametrize(
    "titulo,esperado",
    [
        # Los tres títulos EXACTOS que volcó el run 34939563551 y que el mapa
        # anterior no reconocía. Copiados literalmente del log, no inventados.
        ("Notice of Delisting or Failure to Satisfy a Continued Listing Rule or Standard; Transfer of Listing", "3.01"),
        ("Regulation FD Disclosure", "7.01"),
        ("Financial Statements and Exhibits", "9.01"),
    ],
)
def test_titulos_reales_que_edgar_imprimio_y_no_se_reconocian(titulo, esperado):
    """EDGAR imprime el TÍTULO del Item, nunca el número — confirmado sobre la
    cabecera real. El mapa solo tenía los 8 Items dentro de alcance, así que un
    8-K cuyos Items caían todos fuera no daba NINGÚN código y se contaba como
    'formato desconocido' en vez de 'fuera de alcance'."""
    assert _items_de_cabecera(f"ITEM INFORMATION:\t\t{titulo}") == [esperado]


def test_un_item_fuera_de_alcance_se_reconoce_pero_no_genera_evento():
    """La distinción que se había perdido: reconocer el Item y decidir que no
    interesa son dos cosas distintas. Regulation FD se entiende perfectamente;
    simplemente está fuera del alcance del sistema."""
    items = _items_de_cabecera("ITEM INFORMATION:\t\tRegulation FD Disclosure")
    assert items == ["7.01"]           # se reconoce
    assert classify_event_classes(items) == []  # y aun así no genera evento


def test_todos_los_items_del_mapa_tienen_numero_valido():
    from pipeline.ingest.edgar_scraper import ITEM_TITLE_TO_NUMBER

    for titulo, numero in ITEM_TITLE_TO_NUMBER.items():
        assert re.fullmatch(r"\d\.\d\d", numero), f"{titulo} -> {numero}"
    # Sin números repetidos: dos títulos distintos no pueden ser el mismo Item.
    numeros = list(ITEM_TITLE_TO_NUMBER.values())
    assert len(numeros) == len(set(numeros))


def test_ningun_fragmento_del_mapa_es_substring_de_otro():
    """Los fragmentos se buscan por substring. Si uno estuviera contenido en
    otro, un Item podría clasificarse como el que no es. Se resuelve por la
    coincidencia más larga, pero conviene que ni siquiera se dé el caso."""
    from pipeline.ingest.edgar_scraper import ITEM_TITLE_TO_NUMBER

    fragmentos = list(ITEM_TITLE_TO_NUMBER)
    solapados = [(a, b) for a in fragmentos for b in fragmentos if a != b and a in b]
    assert solapados == []


def test_la_coincidencia_mas_larga_gana_y_no_el_orden_del_diccionario():
    """Con un título que contiene dos fragmentos, debe ganar el más específico
    —no el que salga antes al recorrer el diccionario."""
    from pipeline.ingest import edgar_scraper

    mapa = {"other events": "8.01", "other events of importance": "9.99"}
    original = edgar_scraper.ITEM_TITLE_TO_NUMBER
    try:
        edgar_scraper.ITEM_TITLE_TO_NUMBER = mapa
        assert _items_de_cabecera("ITEM INFORMATION:\t\tOther Events of Importance") == ["9.99"]
    finally:
        edgar_scraper.ITEM_TITLE_TO_NUMBER = original


def test_el_numero_gana_al_titulo_si_la_cabecera_trae_los_dos_formatos():
    """Si algún día EDGAR volviera a imprimir números, la rama numérica sigue
    siendo la primera: es más específica que buscar fragmentos de texto."""
    assert _items_de_cabecera("ITEM INFORMATION:\t\t2.02 Results of Operations") == ["2.02"]


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


# --- Hora real de aceptación y festivos (D0) ---

from pipeline.ingest.edgar_scraper import parse_acceptance_datetime  # noqa: E402


def test_parse_acceptance_datetime_lee_la_hora_et_de_la_cabecera():
    header = (FIXTURES / "sample_8k_header_numeric.txt").read_text()
    dt = parse_acceptance_datetime(header)
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2024, 3, 15, 16, 12)
    assert str(dt.tzinfo) == "America/New_York"


def test_parse_acceptance_datetime_sin_campo_devuelve_none():
    assert parse_acceptance_datetime("<SEC-HEADER>\nACCESSION NUMBER: x\n</SEC-HEADER>") is None


def test_filing_tras_el_cierre_de_un_viernes_tiene_d0_el_lunes():
    """El caso que se perdía con filed_at a medianoche: resultados publicados
    a las 16:12 ET de un viernes. D0 es el lunes, no el viernes."""
    header = (FIXTURES / "sample_8k_header_numeric.txt").read_text()
    assert compute_d0_close_date(parse_acceptance_datetime(header)) == date(2024, 3, 18)


def test_d0_salta_festivos_de_bolsa():
    # Jueves 2024-03-28 a las 17:00 ET -> viernes 29 es Viernes Santo (NYSE
    # cerrado) -> D0 es el lunes 1 de abril.
    assert compute_d0_close_date(datetime(2024, 3, 28, 17, 0)) == date(2024, 4, 1)
    # Filing en pleno 4 de julio (jueves) -> D0 el viernes 5.
    assert compute_d0_close_date(datetime(2024, 7, 4, 10, 0)) == date(2024, 7, 5)
