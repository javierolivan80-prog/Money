"""xbrl_fundamentals.py — cuentas anuales reales desde XBRL de la SEC.

Por qué existe: hasta ahora el proyecto solo tenía EVENTOS (8-K, FDA) y
PRECIOS. Para el análisis de largo plazo (calidad del negocio + valoración)
hacen falta las CUENTAS: ingresos, beneficio, deuda, flujo de caja. Eso
normalmente se compra (Sharadar ~$1.200/año, ver DATA_REQUIREMENTS_PHASED.md
§2.2) — pero la SEC publica exactamente los mismos números gratis en XBRL,
extraídos de los propios 10-K/10-Q:

    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

DISCIPLINA ANTI-LOOK-AHEAD — la parte que importa de este fichero:

Cada hecho XBRL trae DOS fechas y confundirlas es un look-ahead silencioso:
  - `end`   : el período que cubre el dato (ej. ejercicio cerrado 2023-12-31)
  - `filed` : cuándo se presentó, es decir, cuándo el dato se hizo PÚBLICO
              (ej. 2024-02-15)

Los ingresos de 2023 NO se conocían el 31-12-2023. Se conocieron seis semanas
después. Usar `end` como si fuera la fecha de disponibilidad daría al sistema
seis semanas de información del futuro en cada ejercicio — exactamente el
mismo tipo de error que el prior de shrinkage que se corrigió en e1f6c61,
invisible en los precios y con el sesgo apuntando siempre en la misma
dirección. Por eso `filed_at` es NOT NULL en la tabla y es la columna por la
que hay que acotar SIEMPRE al leer esto desde el camino de decisión (mismo
papel que `d0_close_date` para los eventos — ARCHITECTURE_LEAN.md §4).

RESTATEMENTS: el mismo ejercicio puede aparecer varias veces si la empresa lo
reexpresa en un filing posterior. Se conserva la presentación MÁS TEMPRANA de
cada período: es la que estaba disponible en su momento. La versión
reexpresada es información del futuro respecto a cualquier decisión tomada
entre ambas fechas.

DATOS QUE FALTAN: no todas las empresas reportan todas las etiquetas, y la
misma magnitud aparece bajo nombres distintos según el sector y el año (de ahí
las listas de fallback en _CONCEPT_TAGS). Cuando una magnitud no aparece bajo
ninguna etiqueta conocida se guarda NULL — nunca se imputa ni se estima (regla
§91 del spec: si el dato no está, se dice, no se inventa).

ADVERTENCIA DE VALIDACIÓN: data.sec.gov está bloqueado por la política de
egress de este sandbox, igual que el resto de fuentes (AUDIT_LEAN.md §1.5).
El parseo está probado contra un fixture con la forma documentada de la API;
la descarga real solo ocurre en GitHub Actions.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from pipeline.ingest.edgar_http import throttled_get

logger = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Solo ejercicios completos: para juzgar la calidad de un negocio a largo
# plazo, el ruido trimestral estorba más que ayuda (y el 10-K es el documento
# auditado). fp="FY" filtra el ejercicio anual dentro de los 10-K.
ANNUAL_FORM = "10-K"
ANNUAL_PERIOD = "FY"

# Una magnitud contable puede venir bajo varias etiquetas us-gaap según el
# sector, el año y cómo lo etiquete el auditor. Se prueban en orden y gana la
# primera que exista — no se suman ni se promedian entre sí.
_CONCEPT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "shares_outstanding": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "CommonStockSharesOutstanding",
    ),
}

# Las magnitudes monetarias vienen en USD; el número de acciones en "shares".
_CONCEPT_UNITS: dict[str, str] = {concept: "shares" if concept == "shares_outstanding" else "USD" for concept in _CONCEPT_TAGS}


def _extract_concept_by_period(facts: dict, concept: str) -> dict[date, tuple[date, float]]:
    """Para una magnitud, devuelve {fin_de_ejercicio: (fecha_presentacion, valor)}.

    Conserva la presentación MÁS TEMPRANA de cada ejercicio (ver la nota sobre
    restatements en la cabecera del módulo)."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    unit = _CONCEPT_UNITS[concept]

    for tag in _CONCEPT_TAGS[concept]:
        entries = us_gaap.get(tag, {}).get("units", {}).get(unit)
        if not entries:
            continue

        by_period: dict[date, tuple[date, float]] = {}
        for entry in entries:
            if entry.get("form") != ANNUAL_FORM or entry.get("fp") != ANNUAL_PERIOD:
                continue
            if entry.get("end") is None or entry.get("filed") is None or entry.get("val") is None:
                continue
            period_end = date.fromisoformat(entry["end"])
            filed = date.fromisoformat(entry["filed"])
            previous = by_period.get(period_end)
            if previous is None or filed < previous[0]:
                by_period[period_end] = (filed, float(entry["val"]))

        if by_period:
            return by_period

    return {}


def parse_company_facts(facts: dict) -> list[dict]:
    """Convierte el JSON de companyfacts en una fila por ejercicio.

    Separado de la descarga para poder probarse sin red. Una magnitud que no
    aparece bajo ninguna etiqueta conocida queda como None en esa fila — no se
    imputa (ver cabecera del módulo).

    `filed_at` de la fila es la fecha de presentación MÁS TARDÍA entre las
    magnitudes de ese ejercicio: la fila entera no puede considerarse conocida
    hasta que lo está su dato más lento. Tomar la más temprana dejaría la fila
    disponible antes de que todos sus campos existieran, que es look-ahead
    dentro de la propia fila.
    """
    cik = facts.get("cik")
    if cik is None:
        raise ValueError("companyfacts sin campo 'cik'")

    per_concept = {concept: _extract_concept_by_period(facts, concept) for concept in _CONCEPT_TAGS}

    all_periods = sorted({period for values in per_concept.values() for period in values})

    rows = []
    for period_end in all_periods:
        row: dict = {"cik": str(cik), "fiscal_period_end": period_end, "form": ANNUAL_FORM}
        filed_dates = []
        for concept, values in per_concept.items():
            found = values.get(period_end)
            if found is None:
                row[concept] = None
            else:
                filed, value = found
                row[concept] = value
                filed_dates.append(filed)
        if not filed_dates:
            continue  # ejercicio sin ninguna magnitud utilizable
        row["filed_at"] = max(filed_dates)
        rows.append(row)

    return rows


def fetch_company_facts(cik: str) -> dict:
    """Descarga companyfacts de un CIK. Usa el GET rate-limitado compartido:
    data.sec.gov aplica el mismo límite de 10 req/s que el resto de EDGAR y
    exige el mismo User-Agent identificable."""
    url = COMPANYFACTS_URL.format(cik=int(cik))
    resp = throttled_get(url)
    return json.loads(resp.text)


def store_fundamentals(conn, rows: list[dict]) -> int:
    """Upsert por (cik, fiscal_period_end, form) — idempotente, igual que el
    resto de ingestas del proyecto (se reejecuta cada noche)."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO fundamentals (
                cik, fiscal_period_end, filed_at, form, revenue, net_income,
                stockholders_equity, total_assets, total_liabilities, long_term_debt,
                operating_cash_flow, capex, shares_outstanding
            ) VALUES (
                %(cik)s, %(fiscal_period_end)s, %(filed_at)s, %(form)s, %(revenue)s, %(net_income)s,
                %(stockholders_equity)s, %(total_assets)s, %(total_liabilities)s, %(long_term_debt)s,
                %(operating_cash_flow)s, %(capex)s, %(shares_outstanding)s
            )
            ON CONFLICT (cik, fiscal_period_end, form) DO UPDATE SET
                filed_at = EXCLUDED.filed_at,
                revenue = EXCLUDED.revenue,
                net_income = EXCLUDED.net_income,
                stockholders_equity = EXCLUDED.stockholders_equity,
                total_assets = EXCLUDED.total_assets,
                total_liabilities = EXCLUDED.total_liabilities,
                long_term_debt = EXCLUDED.long_term_debt,
                operating_cash_flow = EXCLUDED.operating_cash_flow,
                capex = EXCLUDED.capex,
                shares_outstanding = EXCLUDED.shares_outstanding
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def ingest_universe_fundamentals(conn, limit: int | None = None) -> dict:
    """Recorre los CIK del universo invertible y guarda sus cuentas anuales.

    Un fallo en una empresa concreta (CIK sin XBRL, JSON inesperado) no aborta
    el resto: se registra y se sigue. Con cientos de empresas, que una rompa
    toda la ingesta nocturna sería peor que perder esa empresa.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT cik FROM universe WHERE ticker IS NOT NULL ORDER BY cik"
            + (" LIMIT %s" if limit else ""),
            (limit,) if limit else (),
        )
        ciks = [r["cik"] for r in cur.fetchall()]

    stored, failed = 0, 0
    for cik in ciks:
        try:
            facts = fetch_company_facts(cik)
            rows = parse_company_facts(facts)
            stored += store_fundamentals(conn, rows)
        except Exception as exc:  # noqa: BLE001 — ver docstring
            failed += 1
            logger.warning("Sin fundamentales para CIK %s: %s", cik, exc)

    logger.info("Fundamentales: %d filas guardadas, %d empresas fallidas de %d", stored, failed, len(ciks))
    return {"n_ciks": len(ciks), "n_rows_stored": stored, "n_failed": failed}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    result = ingest_universe_fundamentals(get_connection())
    print(f"{result['n_rows_stored']} ejercicios guardados de {result['n_ciks']} empresas ({result['n_failed']} fallidas)")
