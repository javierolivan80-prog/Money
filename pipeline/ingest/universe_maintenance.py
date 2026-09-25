"""universe_maintenance.py — la lista de empresas: capitalización, liquidez y
si cada una entra en el universo invertible.

POR QUÉ EXISTE: el schema tenía desde la Fase 1 las columnas
universe.market_cap_last_usd, adv_usd_60d e in_investable_universe
(ARCHITECTURE_LEAN.md §10: precio > 5 $, capitalización > 300 M$, volumen
medio diario > 1 M$), pero ningún paso las calculaba: todas las empresas se
quedaban en in_investable_universe = FALSE y sin capitalización. Sin esto no
hay forma de priorizar las empresas grandes ni de acotar el gasto de la IA.

Se calcula con datos que el pipeline YA descarga, sin fuentes nuevas:
  - precio: último close_raw de `prices` (yfinance), si no es más viejo que
    config.MAX_PRICE_STALENESS_DAYS;
  - acciones: último shares_outstanding de `fundamentals` (XBRL de la SEC),
    por filed_at;
  - volumen en $: media de close_raw * volume de las últimas 60 sesiones.

LIMITACIÓN CONOCIDA (documentada, no oculta): las acciones vienen del último
10-K, así que tras un split posterior la capitalización sale infravalorada
hasta el siguiente 10-K. Para el filtro de 300 M$ o para ordenar por tamaño
es irrelevante en empresas grandes; no sirve como dato de valoración fino.

Idempotente: se recalcula entero en cada pasada (una sola sentencia SQL).
"""
from __future__ import annotations

import logging
from datetime import date

from pipeline import config

logger = logging.getLogger(__name__)

ADV_WINDOW_SESSIONS = 60


def is_investable(price: float | None, market_cap: float | None, adv_usd: float | None) -> bool:
    """Regla de ARCHITECTURE_LEAN.md §10. Un dato que falta = no invertible:
    ante la duda, fuera (no se imputa nada)."""
    if price is None or market_cap is None or adv_usd is None:
        return False
    return (
        price >= config.MIN_PRICE_USD
        and market_cap >= config.MIN_MARKET_CAP_USD
        and adv_usd >= config.MIN_ADV_USD
    )


_REFRESH_SQL = """
WITH last_px AS (
    SELECT DISTINCT ON (ticker) ticker, close_raw, trade_date
    FROM prices
    WHERE close_raw IS NOT NULL
    ORDER BY ticker, trade_date DESC
), adv AS (
    SELECT ticker, avg(close_raw * volume) AS adv_usd
    FROM (
        SELECT ticker, close_raw, volume,
               row_number() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS rn
        FROM prices
        WHERE close_raw IS NOT NULL AND volume IS NOT NULL
    ) t
    WHERE rn <= %(adv_window)s
    GROUP BY ticker
), shares AS (
    SELECT DISTINCT ON (cik) cik, shares_outstanding
    FROM fundamentals
    WHERE shares_outstanding > 0
    ORDER BY cik, filed_at DESC
), metrics AS (
    SELECT u.cik,
           CASE WHEN lp.trade_date >= %(as_of)s::date - %(staleness)s THEN lp.close_raw END AS price,
           a.adv_usd,
           s.shares_outstanding
    FROM universe u
    LEFT JOIN last_px lp ON lp.ticker = u.ticker
    LEFT JOIN adv a ON a.ticker = u.ticker
    LEFT JOIN shares s ON s.cik = u.cik
)
UPDATE universe u SET
    market_cap_last_usd = m.price * m.shares_outstanding,
    adv_usd_60d = m.adv_usd,
    in_investable_universe = COALESCE(
        m.price >= %(min_price)s
        AND m.price * m.shares_outstanding >= %(min_cap)s
        AND m.adv_usd >= %(min_adv)s,
        FALSE
    )
FROM metrics m
WHERE m.cik = u.cik
"""


def refresh_universe_metrics(conn, as_of: date | None = None) -> dict:
    """Recalcula capitalización, volumen y el flag de invertible para TODO el
    universo. Devuelve un resumen para el log."""
    as_of = as_of or date.today()
    with conn.cursor() as cur:
        cur.execute(
            _REFRESH_SQL,
            {
                "adv_window": ADV_WINDOW_SESSIONS,
                "as_of": as_of,
                "staleness": config.MAX_PRICE_STALENESS_DAYS,
                "min_price": config.MIN_PRICE_USD,
                "min_cap": config.MIN_MARKET_CAP_USD,
                "min_adv": config.MIN_ADV_USD,
            },
        )
        cur.execute(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE in_investable_universe) AS invertibles,
                   count(*) FILTER (WHERE market_cap_last_usd IS NULL) AS sin_capitalizacion,
                   count(*) FILTER (WHERE market_cap_last_usd >= 10e9) AS grandes_10b,
                   count(*) FILTER (WHERE market_cap_last_usd >= 200e9) AS mega_200b
            FROM universe
            """
        )
        summary = dict(cur.fetchone())
    conn.commit()
    return summary


def top_companies(conn, n: int = 50, min_market_cap: float | None = None) -> list[dict]:
    """La lista: empresas invertibles ordenadas por capitalización, con cuántos
    eventos tienen y cuántos esperan todavía el análisis de la IA."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.ticker, u.company_name, u.market_cap_last_usd, u.adv_usd_60d,
                   count(e.event_id) AS eventos,
                   count(e.event_id) FILTER (WHERE ea.event_id IS NULL) AS pendientes_ia
            FROM universe u
            LEFT JOIN events e ON e.cik = u.cik
            LEFT JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE u.in_investable_universe
              AND u.market_cap_last_usd >= %(min_cap)s
            GROUP BY u.cik
            ORDER BY u.market_cap_last_usd DESC
            LIMIT %(n)s
            """,
            {"min_cap": min_market_cap if min_market_cap is not None else config.MIN_MARKET_CAP_USD, "n": n},
        )
        return cur.fetchall()


def price_tickers(conn) -> list[str]:
    """Tickers cuyo precio hay que descargar: el universo entero MÁS las series
    de referencia de enrichment.py (SPY, ^VIX, ETFs sectoriales), que no son
    empresas y por eso nunca aparecían en `universe`."""
    from pipeline.analyze.enrichment import BENCHMARK_TICKERS

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM universe WHERE ticker IS NOT NULL")
        tickers = {r["ticker"] for r in cur.fetchall()}
    return sorted(tickers | set(BENCHMARK_TICKERS))


def _fmt_usd(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    if v >= 1e9:
        return f"{v / 1e9:,.1f} B$"
    return f"{v / 1e6:,.0f} M$"


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Capitalización/liquidez del universo y lista de empresas")
    parser.add_argument("--list", type=int, default=0, help="Imprime las N mayores empresas invertibles")
    parser.add_argument("--min-cap", type=float, default=None, help="Capitalización mínima para --list (USD)")
    parser.add_argument("--tickers-file", default=None,
                        help="Solo escribe aquí la lista de tickers a descargar (universo + referencias) y sale")
    args = parser.parse_args()

    from pipeline.db.connection import get_connection

    conn = get_connection()
    if args.tickers_file:
        tickers = price_tickers(conn)
        with open(args.tickers_file, "w") as f:
            f.write("\n".join(tickers))
        print(f"{len(tickers)} tickers a descargar (universo + referencias)")
        raise SystemExit(0)
    resumen = refresh_universe_metrics(conn)
    print(
        f"Universo: {resumen['total']} empresas | invertibles: {resumen['invertibles']} | "
        f">=10 B$: {resumen['grandes_10b']} | >=200 B$: {resumen['mega_200b']} | "
        f"sin capitalización (faltan precio o acciones): {resumen['sin_capitalizacion']}"
    )
    if args.list:
        for i, r in enumerate(top_companies(conn, args.list, args.min_cap), 1):
            print(
                f"{i:>3}. {r['ticker']:<6} {r['company_name'][:40]:<40} "
                f"cap {_fmt_usd(r['market_cap_last_usd']):>12}  vol/día {_fmt_usd(r['adv_usd_60d']):>10}  "
                f"eventos {r['eventos']:>4}  pendientes IA {r['pendientes_ia']:>4}"
            )
