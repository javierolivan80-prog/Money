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
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from pipeline import config

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": config.EDGAR_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

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

_RATE_LIMIT_DELAY = 1.0 / config.EDGAR_RATE_LIMIT_PER_SEC


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


def _throttled_get(url: str, **kwargs) -> requests.Response:
    """GET con rate limit fijo y reintentos con backoff exponencial.

    La SEC devuelve 429 si se supera el límite; también hay que tolerar caídas
    de red transitorias. Backoff: 2s, 4s, 8s, 16s (igual que la política de git
    del resto del proyecto, por consistencia).
    """
    delays = [2, 4, 8, 16]
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        time.sleep(_RATE_LIMIT_DELAY)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
            if resp.status_code == 429:
                logger.warning("429 de EDGAR en %s, reintentando", url)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning("Fallo en %s (intento %d): %s", url, attempt, exc)
    raise RuntimeError(f"No se pudo descargar {url} tras reintentos") from last_exc


def daily_index_url(day: date) -> str:
    """URL del índice diario de formularios para una fecha dada.

    Formato real de EDGAR: /Archives/edgar/daily-index/{YYYY}/QTR{n}/form.{YYYYMMDD}.idx
    """
    quarter = (day.month - 1) // 3 + 1
    return (
        f"{config.EDGAR_BASE}/Archives/edgar/daily-index/{day.year}/QTR{quarter}/"
        f"form.{day.strftime('%Y%m%d')}.idx"
    )


def parse_daily_index(raw_text: str) -> list[dict]:
    """Parsea el .idx de ancho fijo de EDGAR.

    Formato (tras la cabecera y una línea de guiones separadora):
      Form Type   Company Name   CIK   Date Filed   File Name
    Columnas de ancho fijo, no separadas por un delimitador único — hay que
    usar las posiciones de la línea de guiones para saber dónde corta cada campo.
    Filtra solo form_type == '8-K' (exacto, para no capturar 8-K/A como si fuera
    igual — las enmiendas se tratan aparte, no en el POC).
    """
    lines = raw_text.splitlines()
    # La línea separadora real de EDGAR es de la forma "---- ---- ----" (grupos
    # de guiones por columna, separados por espacios) — no una tira de guiones
    # continua. set(l.strip()) == {"-"} fallaba contra el formato real por eso.
    def is_separator(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and set(stripped) <= {"-", " "} and "-" in stripped

    sep_idx = next((i for i, l in enumerate(lines) if is_separator(l)), None)
    if sep_idx is None:
        raise ValueError("Formato de daily-index inesperado: no se encontró la línea separadora")

    header = lines[sep_idx - 1]
    sep_line = lines[sep_idx]
    # Cada columna empieza donde EMPIEZA su tramo de guiones (no donde termina):
    # los tramos de guiones están separados por espacios, y cortar en el inicio
    # de cada tramo deja el espacio de separación como parte del campo previo,
    # que igualmente se descarta con .strip() en cut().
    col_starts = [
        i for i in range(len(sep_line)) if sep_line[i] == "-" and (i == 0 or sep_line[i - 1] != "-")
    ]

    def cut(line: str, i: int) -> str:
        # La última columna (File Name) es de longitud variable y más larga que
        # su cabecera de dashes — corta hasta el final real de la línea de datos,
        # no hasta una posición fija derivada de la cabecera.
        end = col_starts[i + 1] if i + 1 < len(col_starts) else len(line)
        return line[col_starts[i] : end].strip()

    rows = []
    for line in lines[sep_idx + 1 :]:
        if not line.strip():
            continue
        form_type = cut(line, 0)
        if form_type != "8-K":
            continue
        rows.append(
            {
                "form_type": form_type,
                "company_name": cut(line, 1),
                "cik": cut(line, 2).lstrip("0") or "0",
                "date_filed": cut(line, 3),
                "file_name": cut(line, 4),
            }
        )
    return rows


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
    resp = _throttled_get(url)
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
    resp = _throttled_get(daily_index_url(day))
    rows = parse_daily_index(resp.text)
    logger.info("%d formularios 8-K encontrados el %s", len(rows), day.isoformat())

    filings = []
    for row in rows:
        try:
            accession, item_codes = fetch_filing_item_codes(row["file_name"])
        except RuntimeError as exc:
            logger.error("Saltando %s: %s", row["file_name"], exc)
            continue
        if not classify_event_classes(item_codes):
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
