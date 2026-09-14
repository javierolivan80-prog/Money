"""quality_score.py — análisis fundamental de largo plazo, explicado en cristiano.

Responde una pregunta distinta a la del resto del proyecto. El motor de
eventos pregunta "¿esta noticia mueve el precio los próximos 20 días?". Esto
pregunta "¿es este un buen negocio a un precio razonable?" — horizonte de
años, no de días, y sobre las cuentas auditadas (tabla `fundamentals`,
XBRL de la SEC), no sobre eventos.

CINCO CRITERIOS, en el orden en que los miraría alguien que analiza negocios
y no gráficos:

  1. Rentabilidad      — ¿gana dinero sobre el capital que emplea? (ROE)
  2. Solidez           — ¿está endeudada hasta las cejas? (deuda/fondos propios)
  3. Calidad del beneficio — ¿el beneficio contable se convierte en caja de
                         verdad? (flujo de caja libre / beneficio neto)
  4. Crecimiento       — ¿vende más cada año? (CAGR de ingresos)
  5. Precio            — ¿está caro? (PER, o precio/flujo de caja libre)

Los tres primeros miden el NEGOCIO; el cuarto, su trayectoria; el quinto es
lo único que depende del precio de la acción. Un negocio excelente a un
precio absurdo no es una buena inversión, y esa distinción es justo la que se
pierde cuando alguien mira solo el gráfico.

SOBRE LOS UMBRALES: son convenciones del análisis fundamental clásico (ROE
>15% "bueno", deuda/equity <0.5 "conservador"), no constantes derivadas de
este dataset ni optimizadas sobre él. Se documentan como lo que son —
heurísticas razonables y explicables— igual que los umbrales de
portfolio_report.py:generate_recommendation. Optimizarlas contra el propio
histórico sería sobreajuste disfrazado de rigor.

DATOS QUE FALTAN: un componente que no se puede calcular (la empresa no
reporta esa magnitud) NO puntúa 0 — se excluye y se renormalizan los pesos
de los demás. Tratar "no lo sé" como "es malo" sesgaría la nota justo en las
empresas con menos información, que es lo contrario de lo que se quiere.
Mismo principio que analyze/novelty.py con sus componentes no disponibles.

ESTO NO ES UNA RECOMENDACIÓN DE INVERSIÓN. Es un resumen estructurado de
cuentas públicas. Ver el aviso en la propia interfaz.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

# Pesos de cada criterio sobre la nota final (suman 100 si están todos
# disponibles; si falta alguno se renormaliza sobre los presentes).
_WEIGHTS = {
    "rentabilidad": 25.0,
    "solidez": 20.0,
    "calidad_beneficio": 20.0,
    "crecimiento": 20.0,
    "precio": 15.0,
}

# Umbrales (ver nota en la cabecera: convenciones, no optimizados).
ROE_EXCELLENT = 0.20   # 20% sobre fondos propios
ROE_GOOD = 0.15
ROE_POOR = 0.05

DEBT_EQUITY_CONSERVATIVE = 0.5
DEBT_EQUITY_HIGH = 1.5

FCF_CONVERSION_GOOD = 0.9   # el FCF es >=90% del beneficio contable
FCF_CONVERSION_POOR = 0.4

REVENUE_CAGR_STRONG = 0.10
REVENUE_CAGR_FLAT = 0.0

PE_CHEAP = 12.0
PE_FAIR = 20.0
PE_EXPENSIVE = 35.0

MIN_YEARS_FOR_GROWTH = 3  # con menos de 3 ejercicios, un CAGR es ruido


@dataclass
class Component:
    name: str
    score: float | None       # 0-100, None si no se puede calcular
    raw_value: float | None   # el número crudo (ROE, deuda/equity, ...)
    explanation: str          # en cristiano, para mostrar tal cual en la UI


@dataclass
class QualityScore:
    total: float | None                  # 0-100, None si no hay ni un componente
    components: list[Component] = field(default_factory=list)
    n_years: int = 0
    verdict: str = ""

    @property
    def unavailable(self) -> list[str]:
        return [c.name for c in self.components if c.score is None]

    def as_json(self) -> dict:
        return {
            "total": round(self.total, 1) if self.total is not None else None,
            "n_years": self.n_years,
            "verdict": self.verdict,
            "components": [
                {"name": c.name, "score": round(c.score, 1) if c.score is not None else None, "raw_value": c.raw_value, "explanation": c.explanation}
                for c in self.components
            ],
            "unavailable": self.unavailable,
        }


def _scale(value: float, low: float, high: float) -> float:
    """Mapea [low, high] -> [0, 100], saturando fuera del rango. low puede ser
    mayor que high (escala invertida: menos es mejor, como en deuda o PER)."""
    if high == low:
        return 50.0
    pct = (value - low) / (high - low) * 100.0
    return max(0.0, min(100.0, pct))


def _score_rentabilidad(net_income: float | None, equity: float | None) -> Component:
    if net_income is None or equity is None or equity <= 0:
        return Component("Rentabilidad", None, None, "No se puede calcular: faltan beneficio neto o fondos propios (o los fondos propios son negativos).")
    roe = net_income / equity
    score = _scale(roe, ROE_POOR, ROE_EXCELLENT)
    if roe >= ROE_EXCELLENT:
        txt = f"Excelente: gana {roe * 100:.0f}€ al año por cada 100€ de capital propio."
    elif roe >= ROE_GOOD:
        txt = f"Buena: gana {roe * 100:.0f}€ al año por cada 100€ de capital propio."
    elif roe > 0:
        txt = f"Floja: solo gana {roe * 100:.1f}€ al año por cada 100€ de capital propio."
    else:
        txt = f"Pierde dinero: {roe * 100:.1f}% sobre el capital propio."
    return Component("Rentabilidad", score, roe, txt)


def _score_solidez(long_term_debt: float | None, equity: float | None) -> Component:
    if equity is None or equity <= 0:
        return Component("Solidez financiera", None, None, "No se puede calcular: no hay fondos propios utilizables.")
    debt = long_term_debt if long_term_debt is not None else 0.0
    ratio = debt / equity
    # Escala invertida: menos deuda es mejor.
    score = _scale(ratio, DEBT_EQUITY_HIGH, DEBT_EQUITY_CONSERVATIVE)
    if ratio <= DEBT_EQUITY_CONSERVATIVE:
        txt = f"Sólida: debe {ratio:.2f}€ por cada euro de capital propio — poco apalancada."
    elif ratio <= DEBT_EQUITY_HIGH:
        txt = f"Aceptable: debe {ratio:.2f}€ por cada euro de capital propio."
    else:
        txt = f"Muy endeudada: debe {ratio:.2f}€ por cada euro de capital propio."
    return Component("Solidez financiera", score, ratio, txt)


def _score_calidad_beneficio(operating_cash_flow: float | None, capex: float | None, net_income: float | None) -> Component:
    if operating_cash_flow is None or net_income is None or net_income <= 0:
        return Component("Calidad del beneficio", None, None, "No se puede calcular: falta el flujo de caja operativo o la empresa no tuvo beneficio.")
    fcf = operating_cash_flow - (capex if capex is not None else 0.0)
    conversion = fcf / net_income
    score = _scale(conversion, FCF_CONVERSION_POOR, FCF_CONVERSION_GOOD)
    if conversion >= FCF_CONVERSION_GOOD:
        txt = f"El beneficio es caja real: por cada 100€ de beneficio contable entran {conversion * 100:.0f}€ de caja libre."
    elif conversion > 0:
        txt = f"Ojo: por cada 100€ de beneficio contable solo entran {conversion * 100:.0f}€ de caja libre."
    else:
        txt = "El beneficio contable no se convierte en caja: el flujo de caja libre es negativo."
    return Component("Calidad del beneficio", score, conversion, txt)


def _score_crecimiento(revenues_oldest_to_newest: list[float]) -> Component:
    usable = [r for r in revenues_oldest_to_newest if r is not None and r > 0]
    if len(usable) < MIN_YEARS_FOR_GROWTH:
        return Component("Crecimiento", None, None, f"No se puede calcular: hacen falta al menos {MIN_YEARS_FOR_GROWTH} ejercicios con ingresos y solo hay {len(usable)}.")
    first, last = usable[0], usable[-1]
    years = len(usable) - 1
    cagr = (last / first) ** (1 / years) - 1
    score = _scale(cagr, -REVENUE_CAGR_STRONG, REVENUE_CAGR_STRONG)
    if cagr >= REVENUE_CAGR_STRONG:
        txt = f"Crece con fuerza: los ingresos suben un {cagr * 100:.1f}% al año de media en los últimos {years + 1} ejercicios."
    elif cagr > REVENUE_CAGR_FLAT:
        txt = f"Crece despacio: los ingresos suben un {cagr * 100:.1f}% al año de media."
    else:
        txt = f"Se encoge: los ingresos bajan un {abs(cagr) * 100:.1f}% al año de media."
    return Component("Crecimiento", score, cagr, txt)


def _score_precio(price: float | None, shares: float | None, net_income: float | None) -> Component:
    if price is None or shares is None or shares <= 0:
        return Component("Precio", None, None, "No se puede calcular: falta el precio de la acción o el número de acciones.")
    if net_income is None or net_income <= 0:
        return Component("Precio", None, None, "No se puede calcular un PER: la empresa no tuvo beneficio en el último ejercicio disponible.")
    market_cap = price * shares
    pe = market_cap / net_income
    # Escala invertida: un PER más bajo puntúa mejor.
    score = _scale(pe, PE_EXPENSIVE, PE_CHEAP)
    if pe <= PE_CHEAP:
        txt = f"Barata: se paga {pe:.1f}€ por cada euro de beneficio anual."
    elif pe <= PE_FAIR:
        txt = f"Precio razonable: se paga {pe:.1f}€ por cada euro de beneficio anual."
    elif pe <= PE_EXPENSIVE:
        txt = f"Cara: se paga {pe:.1f}€ por cada euro de beneficio anual."
    else:
        txt = f"Muy cara: se paga {pe:.1f}€ por cada euro de beneficio anual — el mercado descuenta mucho crecimiento futuro."
    return Component("Precio", score, pe, txt)


def _verdict(total: float | None, n_available: int) -> str:
    if total is None:
        return "Sin datos suficientes para valorar esta empresa."
    if n_available < 3:
        return "Datos incompletos — esta nota se apoya en muy pocos criterios, tómala con reservas."
    if total >= 75:
        return "Negocio sólido a un precio razonable según sus propias cuentas."
    if total >= 55:
        return "Negocio decente, con algún punto flojo — mira el desglose."
    if total >= 35:
        return "Tiene problemas visibles en varios criterios."
    return "Flojo en casi todos los criterios."


def compute_quality_score(
    annual_rows_oldest_to_newest: list[dict],
    current_price: float | None = None,
) -> QualityScore:
    """Punto de entrada. `annual_rows_oldest_to_newest` son filas de la tabla
    `fundamentals` de UNA empresa, ordenadas del ejercicio más antiguo al más
    reciente, y YA filtradas por el caller según filed_at (anti-look-ahead: el
    caller decide qué se sabía en qué momento; esta función es pura y no tiene
    forma de saberlo).

    `current_price` es el precio por acción con el que valorar. None -> el
    componente de precio queda como no disponible y la nota se renormaliza
    sobre los otros cuatro.
    """
    if not annual_rows_oldest_to_newest:
        return QualityScore(total=None, components=[], n_years=0, verdict=_verdict(None, 0))

    latest = annual_rows_oldest_to_newest[-1]

    components = [
        _score_rentabilidad(latest.get("net_income"), latest.get("stockholders_equity")),
        _score_solidez(latest.get("long_term_debt"), latest.get("stockholders_equity")),
        _score_calidad_beneficio(latest.get("operating_cash_flow"), latest.get("capex"), latest.get("net_income")),
        _score_crecimiento([r.get("revenue") for r in annual_rows_oldest_to_newest]),
        _score_precio(current_price, latest.get("shares_outstanding"), latest.get("net_income")),
    ]

    key_by_name = {
        "Rentabilidad": "rentabilidad",
        "Solidez financiera": "solidez",
        "Calidad del beneficio": "calidad_beneficio",
        "Crecimiento": "crecimiento",
        "Precio": "precio",
    }

    available = [c for c in components if c.score is not None]
    if not available:
        return QualityScore(total=None, components=components, n_years=len(annual_rows_oldest_to_newest), verdict=_verdict(None, 0))

    # Renormalización sobre los componentes disponibles — ver cabecera.
    total_weight = sum(_WEIGHTS[key_by_name[c.name]] for c in available)
    total = sum(c.score * _WEIGHTS[key_by_name[c.name]] for c in available) / total_weight

    return QualityScore(
        total=total,
        components=components,
        n_years=len(annual_rows_oldest_to_newest),
        verdict=_verdict(total, len(available)),
    )


def _fetch_latest_price(conn, ticker: str, as_of_date: date | None = None) -> float | None:
    """Último cierre ajustado conocido. prices no está en la lista de tablas
    del guard anti-look-ahead porque su propia clave ES la fecha (trade_date):
    acotarla es trivial y se hace aquí."""
    with conn.cursor() as cur:
        if as_of_date is None:
            cur.execute(
                "SELECT close_raw, adj_factor FROM prices WHERE ticker = %s AND close_raw IS NOT NULL "
                "ORDER BY trade_date DESC LIMIT 1",
                (ticker,),
            )
        else:
            cur.execute(
                "SELECT close_raw, adj_factor FROM prices WHERE ticker = %s AND close_raw IS NOT NULL "
                "AND trade_date <= %s ORDER BY trade_date DESC LIMIT 1",
                (ticker, as_of_date),
            )
        row = cur.fetchone()
    if row is None or row["close_raw"] is None:
        return None
    adj = float(row["adj_factor"]) if row["adj_factor"] is not None else 1.0
    return float(row["close_raw"]) * adj


def run_quality_screen(conn, as_of_date: date | None = None) -> dict:
    """Calcula y guarda la nota de todas las empresas con cuentas disponibles.

    Pensado para el cron nocturno. as_of_date=None -> hoy (vista en vivo); ver
    la advertencia de fetch_annual_rows sobre backtests.
    """
    effective_date = as_of_date or date.today()

    with conn.cursor() as cur:
        # Acotada por filed_at aunque sea "solo" la cola de trabajo: con una
        # as_of_date pasada, una empresa cuyas cuentas se presentaron DESPUÉS
        # no debe ni aparecer en la lista — si apareciera, fetch_annual_rows
        # devolvería [] y se colaría una fila de "sin datos" que en realidad
        # significa "todavía no se sabía". El guard anti-look-ahead
        # (test_no_lookahead_guard.py) cazó esta query sin acotar.
        cur.execute(
            """
            SELECT DISTINCT u.cik, u.ticker
            FROM universe u
            JOIN fundamentals f ON f.cik = u.cik
            WHERE u.ticker IS NOT NULL
              AND f.filed_at <= %(as_of)s
            ORDER BY u.cik
            """,
            {"as_of": effective_date},
        )
        companies = [(r["cik"], r["ticker"]) for r in cur.fetchall()]

    stored = 0
    for cik, ticker in companies:
        rows = fetch_annual_rows(conn, cik, as_of_date=as_of_date)
        if not rows:
            continue
        price = _fetch_latest_price(conn, ticker, as_of_date=as_of_date)
        score = compute_quality_score(rows, current_price=price)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quality_scores (cik, as_of_date, total_score, verdict, components, n_years, price_used)
                VALUES (%(cik)s, %(as_of)s, %(total)s, %(verdict)s, %(components)s, %(n_years)s, %(price)s)
                ON CONFLICT (cik, as_of_date) DO UPDATE SET
                    total_score = EXCLUDED.total_score,
                    verdict = EXCLUDED.verdict,
                    components = EXCLUDED.components,
                    n_years = EXCLUDED.n_years,
                    price_used = EXCLUDED.price_used,
                    computed_at = now()
                """,
                {
                    "cik": cik,
                    "as_of": effective_date,
                    "total": score.total,
                    "verdict": score.verdict,
                    "components": json.dumps(score.as_json()["components"]),
                    "n_years": score.n_years,
                    "price": price,
                },
            )
        stored += 1
    conn.commit()

    logger.info("Notas de calidad: %d empresas evaluadas (as_of=%s)", stored, effective_date)
    return {"as_of_date": effective_date.isoformat(), "n_companies": len(companies), "n_scored": stored}


def fetch_annual_rows(conn, cik: str, as_of_date: date | None = None) -> list[dict]:
    """Ejercicios de una empresa, del más antiguo al más reciente, acotados
    por filed_at <= as_of_date.

    El filtro por filed_at (NO por fiscal_period_end) es la disciplina
    anti-look-ahead de esta tabla — ver la cabecera de
    ingest/xbrl_fundamentals.py. as_of_date=None significa "todo lo publicado
    hasta hoy", que es lo correcto para la vista en vivo del dashboard, pero
    NUNCA para un backtest: ahí hay que pasar la fecha simulada.
    """
    with conn.cursor() as cur:
        if as_of_date is None:
            cur.execute(
                "SELECT * FROM fundamentals WHERE cik = %s ORDER BY fiscal_period_end",
                (cik,),
            )
        else:
            cur.execute(
                "SELECT * FROM fundamentals WHERE cik = %s AND filed_at <= %s ORDER BY fiscal_period_end",
                (cik, as_of_date),
            )
        return [dict(r) for r in cur.fetchall()]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    result = run_quality_screen(get_connection())
    print(f"{result['n_scored']} empresas evaluadas de {result['n_companies']} con cuentas (as_of={result['as_of_date']})")
