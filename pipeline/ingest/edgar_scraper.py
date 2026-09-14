"""edgar_scraper.py — descarga y normaliza 8-K de EDGAR (5 años, Item 2.02 + satélite).

DISEÑO (ver AUDIT_LEAN.md §2.2.2 y ARCHITECTURE_LEAN.md §3):
  - Fuente: daily-index de EDGAR (https://www.sec.gov/Archives/edgar/daily-index/),
    no full-text search. El índice diario es el censo autoritativo y completo;
    full-text search tiene su propio rate limit más agresivo y cubre menos histórico.
  - Se procesa día a día, no trimestre a trimestre, porque el pipeline es un batch
    NOCTURNO (ARCHITECTURE_LEAN.md §2.5): cada noche solo hay que pedir el índice
    del día anterior. El backfill de 5 años simplemente itera esa misma función
    sobre el rango de fechas histórico.
  - Rate limit: la SEC exige <=10 req/s y un User-Agent con contacto real
    (config.EDGAR_USER_AGENT). Sin eso, 403 inmediato.
  - Idempotente: UNIQUE(source, accession_number, event_class) en la tabla events
    hace que reejecutar el mismo día no duplique nada (INSERT ... ON CONFLICT).
  - Clasificación por regla, no por LLM, para el 8-K: el número de Item viene en el
    propio filing index. Item 8.01 ("otros eventos") es la única clase ambigua y
    se deja marcada para el paso de analyze/ (LLM) — no se decide aquí.

ADVERTENCIA DE VALIDACIÓN — leer antes de confiar en este archivo:
  Este sandbox de desarrollo tiene el egress bloqueado a www.sec.gov (verificado,
  ver AUDIT_LEAN.md §1.5). Este código sigue el formato documentado y estable de
  EDGAR (daily-index .idx + JSON de submissions), pero NO se ha podido ejecutar
  contra el servidor real desde aquí. Antes de confiar en un backfill completo:
    1. Ejecuta `test_edgar_parser.py`, que valida el parser contra un fixture
       offline con el formato real de un daily-index.
    2. Ejecuta este script manualmente para UN SOLO DÍA reciente y verifica a mano
       2-3 filings contra https://www.sec.gov/cgi-bin/browse-edgar antes de lanzar
       el backfill de 5 años completo.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pipeline import config
from pipeline.ingest.edgar_http import throttled_get

logger = logging.getLogger(__name__)

# Mapa de Item de 8-K -> event_class. Cobertura deliberadamente acotada a las
# clases con n suficiente para tener potencia estadística (AUDIT_LEAN.md §2.2.3).
# Todo lo que no está aquí cae en '8K_8.01_OTHER' si trae el item 8.01, o se
# descarta si no trae ninguno de los items relevantes.
ITEM_TO_EVENT_CLASS = {
    "2.02": "8K_2.02_EARNINGS",
    "1.01": "8K_1.01_MATERIAL_AGMT",
    "2.01": "8K_1.01_MATERIAL_AGMT",  # completion of acquisition — misma clase de M&A
    "4.02": "8K_4.02_RESTATEMENT",
    "5.02": "8K_5.02_MGMT_CHANGE",
    "1.03": "8K_1.03_BANKRUPTCY",
    "4.01": "8K_4.01_AUDITOR_CHANGE",
    "8.01": "8K_8.01_OTHER",
}

@dataclass
class RawFiling:
    accession_number: str
    cik: str
    company_name: str
    form_type: str
    filed_at: datetime
    item_codes: list[str]
    source_url: str
    raw_text_hash: str


def daily_index_url(day: date) -> str:
    """URL del índice diario de formularios para una fecha dada.

    Formato real de EDGAR: /Archives/edgar/daily-index/{YYYY}/QTR{n}/form.{YYYYMMDD}.idx
    """
    quarter = (day.month - 1) // 3 + 1
    return (
        f"{config.EDGAR_BASE}/Archives/edgar/daily-index/{day.year}/QTR{quarter}/"
        f"form.{day.strftime('%Y%m%d')}.idx"
    )


# Una fila de datos del daily-index, reconocida por su ESTRUCTURA y no por la
# posición de sus columnas: tipo de formulario, nombre de empresa, CIK
# (dígitos), fecha ISO y ruta del fichero, separados por 2+ espacios.
#
# POR QUÉ ASÍ Y NO POR POSICIONES (bug real, encontrado en producción el
# 2026-09-14): la versión anterior derivaba el corte de cada columna de la
# línea de guiones, asumiendo que EDGAR la publica en tramos por columna
# ("---- ---- ----"). Cuando el separador es en cambio una TIRA CONTINUA de
# guiones, esa lógica colapsa a una sola columna, form_type pasa a ser la línea
# entera, y la comparación `form_type != "8-K"` descarta TODAS las filas. El
# resultado era 0 eventos cada día, con HTTP 200 y sin una sola excepción: el
# pipeline entero corría en verde sobre una base de datos vacía.
#
# El fixture de los tests se había escrito con el formato segmentado (nunca se
# pudo descargar el fichero real desde el sandbox, egress bloqueado), así que
# los tests confirmaban la suposición equivocada en vez de contrastarla.
# Anclarse en el CIK y la fecha ISO hace el parseo independiente de anchos y
# de la forma del separador.
_ROW_RE = re.compile(
    r"^(?P<form_type>\S.*?)\s{2,}"        # tipo de formulario
    r"(?P<company_name>\S.*?)\s{2,}"      # nombre (puede contener espacios simples)
    r"(?P<cik>\d{1,10})\s+"               # CIK
    r"(?P<date_filed>\d{4}-\d{2}-\d{2})\s+"  # fecha de presentación
    r"(?P<file_name>\S+)\s*$"             # ruta del documento
)


def parse_daily_index(raw_text: str) -> list[dict]:
    """Parsea el .idx diario de EDGAR y devuelve solo los 8-K.

    Independiente del ancho de columna y de la forma de la línea separadora
    (ver _ROW_RE). Filtra form_type == '8-K' exacto, para no capturar 8-K/A:
    las enmiendas se tratan aparte, no en el POC.

    Lanza si NINGUNA línea del fichero tiene forma de fila de datos. Un
    daily-index de un día hábil siempre trae filings de algún tipo; cero
    filas reconocibles significa que el formato cambió o que la descarga no es
    lo que se espera, y eso tiene que romper el pipeline en vez de dejarlo
    correr en verde sobre una base vacía — que es exactamente lo que pasó
    cuando esto devolvía [] en silencio.
    """
    all_rows, eight_k = 0, []
    for line in raw_text.splitlines():
        match = _ROW_RE.match(line)
        if match is None:
            continue
        all_rows += 1
        if match.group("form_type") != "8-K":
            continue
        eight_k.append(
            {
                "form_type": match.group("form_type"),
                "company_name": match.group("company_name").strip(),
                "cik": match.group("cik").lstrip("0") or "0",
                "date_filed": match.group("date_filed"),
                "file_name": match.group("file_name"),
            }
        )

    if all_rows == 0:
        raise ValueError(
            "Formato de daily-index inesperado: ninguna línea tiene forma de fila de datos "
            "(tipo, empresa, CIK, fecha, fichero). ¿Cambió el formato de EDGAR?"
        )

    return eight_k


# Títulos oficiales de Item de la Form 8-K (definidos por la propia SEC, estables
# por regulación — a diferencia del formato exacto de la cabecera SGML, esto no
# cambia). Se usan como fallback si la cabecera imprime el título en vez del
# número: ver advertencia en fetch_filing_item_codes.
ITEM_TITLE_TO_NUMBER = {
    "entry into a material definitive agreement": "1.01",
    "completion of acquisition or disposition of assets": "2.01",
    "results of operations and financial condition": "2.02",
    "bankruptcy or receivership": "1.03",
    "changes in registrant's certifying accountant": "4.01",
    "non-reliance on previously issued financial statements": "4.02",
    "departure of directors or certain officers": "5.02",
    "other events": "8.01",
}


def fetch_filing_item_codes(file_name: str) -> tuple[str, list[str]]:
    """Descarga el .txt de submission completo y extrae accession number + Items.

    El nombre de fichero del .idx apunta al submission completo
    (.../{accession-sin-guiones}.txt), que en su cabecera SGML incluye una línea
    "ITEM INFORMATION:" por cada Item reportado en el 8-K.

    ADVERTENCIA — sin verificar en vivo (egress bloqueado, AUDIT_LEAN.md §1.5):
    no hay certeza de si esa línea imprime el NÚMERO del Item ("2.02") o su
    TÍTULO ("Results of Operations and Financial Condition"). Por eso se
    intentan ambos extractores y se combinan resultados: el numérico primero
    (más específico), y el de título como red de seguridad usando
    ITEM_TITLE_TO_NUMBER. Verificar contra 2-3 filings reales antes del
    backfill completo (ver --single-day en el bloque __main__).
    """
    url = f"{config.EDGAR_BASE}/{file_name}"
    resp = throttled_get(url)
    text = resp.text
    accession_match = re.search(r"ACCESSION NUMBER:\s*(\S+)", text)
    accession = accession_match.group(1) if accession_match else file_name

    items: list[str] = []
    for line in re.findall(r"ITEM INFORMATION:\s*(.+)", text):
        line = line.strip()
        numeric = re.match(r"^(\d\.\d\d)\b", line)
        if numeric:
            items.append(numeric.group(1))
            continue
        title_key = line.lower().rstrip(".")
        for title, number in ITEM_TITLE_TO_NUMBER.items():
            if title in title_key:
                items.append(number)
                break
    return accession, items


def classify_event_classes(item_codes: list[str]) -> list[str]:
    """Un 8-K puede traer varios Items; genera un evento normalizado por cada
    Item relevante presente (un 8-K con 2.02 y 9.01 solo genera EARNINGS, ya
    que 9.01 es solo el anexo de exhibits y no es una clase de evento en sí).
    """
    classes = []
    for item in item_codes:
        cls = ITEM_TO_EVENT_CLASS.get(item)
        if cls and cls not in classes:
            classes.append(cls)
    return classes


def compute_d0_close_date(filed_at: datetime) -> date:
    """D0 = fecha en que el evento se considera público al cierre de mercado.

    Regla (ARCHITECTURE_LEAN.md §4): si el filing llega antes del cierre (16:00 ET)
    en un día hábil, D0 es ese mismo día. Si llega después del cierre, o es
    fin de semana, D0 pasa al siguiente día hábil.
    NOTA: EDGAR reporta la hora de aceptación en ET en el submission header
    (campo "ACCEPTANCE-DATETIME"), no en el .idx. Este cálculo asume que
    `filed_at` ya viene en hora ET — responsabilidad del caller.
    """
    d = filed_at.date()
    if filed_at.hour >= 16:
        d += timedelta(days=1)
    while d.weekday() >= 5:  # sábado=5, domingo=6
        d += timedelta(days=1)
    return d


def scrape_day(day: date) -> list[RawFiling]:
    """Descarga y normaliza todos los 8-K relevantes de un día concreto."""
    logger.info("Descargando daily-index de %s", day.isoformat())
    resp = throttled_get(daily_index_url(day))
    rows = parse_daily_index(resp.text)
    logger.info("%d formularios 8-K encontrados el %s", len(rows), day.isoformat())

    filings = []
    # Contadores de descarte. Sin esto, un 8-K que se descarga y luego se tira
    # es invisible: el log solo decía cuántos se ENCONTRARON, no cuántos
    # sobrevivían hasta la base de datos. Con el parser del índice arreglado
    # aparecieron cientos de filings al día y aun así no llegaba ninguno —
    # imposible de localizar sin saber en cuál de los dos filtros caían.
    sin_items, sin_clase = 0, 0
    for row in rows:
        try:
            accession, item_codes = fetch_filing_item_codes(row["file_name"])
        except RuntimeError as exc:
            logger.error("Saltando %s: %s", row["file_name"], exc)
            continue
        if not item_codes:
            sin_items += 1
            if sin_items <= 3:
                # Volcado de diagnóstico de los primeros casos: si la cabecera
                # SGML no trae 'ITEM INFORMATION:' con el formato esperado, el
                # pipeline entero se queda sin eventos y hay que VER el formato
                # real para arreglarlo (ver ADVERTENCIA en
                # fetch_filing_item_codes: nunca se pudo validar contra un
                # filing de verdad desde el sandbox).
                logger.warning("Sin Items extraídos de %s — revisar formato de cabecera", row["file_name"])
            continue
        if not classify_event_classes(item_codes):
            sin_clase += 1
            continue  # ninguna clase relevante, se descarta (no es data loss: es scope)
        raw_hash = hashlib.sha256(f"{accession}:{sorted(item_codes)}".encode()).hexdigest()
        filed_at = datetime.strptime(row["date_filed"], "%Y-%m-%d")
        filings.append(
            RawFiling(
                accession_number=accession,
                cik=row["cik"],
                company_name=row["company_name"],
                form_type=row["form_type"],
                filed_at=filed_at,
                item_codes=item_codes,
                source_url=f"{config.EDGAR_BASE}/{row['file_name']}",
                raw_text_hash=raw_hash,
            )
        )

    if rows and not filings:
        # Todos los 8-K del día descargados y ninguno sobrevive: eso no es
        # "scope", es un parser roto. Que se vea en el log como lo que es.
        logger.warning(
            "%s: %d formularios 8-K descargados y NINGUNO utilizable "
            "(%d sin Items extraídos, %d sin clase de evento relevante)",
            day.isoformat(), len(rows), sin_items, sin_clase,
        )
    else:
        logger.info(
            "%s: %d de %d formularios utilizables (%d sin Items, %d sin clase relevante)",
            day.isoformat(), len(filings), len(rows), sin_items, sin_clase,
        )
    return filings


def backfill_range(start: date, end: date) -> None:
    """Itera día a día sobre el rango histórico. Resumible: si se corta a mitad,
    volver a llamar con start=último día no procesado (comprobar en la BD qué
    fechas ya están cubiertas antes de rellamar, para no re-descargar).
    """
    from pipeline.db.connection import get_connection, upsert_events, upsert_universe_entries

    day = start
    conn = get_connection()
    total = 0
    while day <= end:
        if day.weekday() < 5:  # solo días hábiles; EDGAR no publica índice en fin de semana
            try:
                filings = scrape_day(day)
                upsert_universe_entries(conn, filings)
                total += upsert_events(conn, filings, classify_event_classes, compute_d0_close_date)
            except Exception:
                logger.exception("Fallo procesando %s — continuando con el siguiente día", day)
        day += timedelta(days=1)
    logger.info("Backfill completo: %d eventos insertados/actualizados", total)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Scraper de 8-K de EDGAR")
    parser.add_argument("--start", type=str, default=config.BACKTEST_START)
    parser.add_argument("--end", type=str, default=config.BACKTEST_END)
    parser.add_argument(
        "--single-day",
        type=str,
        default=None,
        help="Verificación manual de UN día antes del backfill completo (ver docstring)",
    )
    args = parser.parse_args()

    if args.single_day:
        d = datetime.strptime(args.single_day, "%Y-%m-%d").date()
        result = scrape_day(d)
        print(f"{len(result)} filings 8-K relevantes en {d}:")
        for f in result[:20]:
            print(f"  {f.company_name} (CIK {f.cik}) — items {f.item_codes} — {f.source_url}")
    else:
        backfill_range(
            datetime.strptime(args.start, "%Y-%m-%d").date(),
            datetime.strptime(args.end, "%Y-%m-%d").date(),
        )
