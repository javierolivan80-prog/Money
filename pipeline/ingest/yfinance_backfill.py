"""yfinance_backfill.py — panel de precios diario, con flags de supervivencia.

INSTRUCCIÓN EXPLÍCITA DEL USUARIO: "flagea tickers deslistados como
'WARNING — posible data gap'. No intentes llenar gaps (es trabajo de Tiingo,
fase 2)." Este archivo hace exactamente eso y nada más: detecta, marca,
nunca interpola ni rellena.

DISEÑO (ver AUDIT_LEAN.md §2.4 y ARCHITECTURE_LEAN.md §4, §7):
  - close_raw + adj_factor se guardan por separado. yfinance recalcula los
    cierres ajustados retroactivamente por splits/dividendos; guardar solo
    el ajustado rompe la reproducibilidad del backtest de un día para otro.
  - captured_at registra CUÁNDO se descargó, no la fecha del precio — permite
    detectar si una fila fue recalculada en una descarga posterior.
  - survivorship_warning = TRUE cuando, para un ticker con eventos en el rango
    solicitado, faltan filas en fechas de calendario bursátil esperadas
    DENTRO de ese rango. Esto es una señal, no una corrección.
  - Descarga por lotes con pausa entre lotes + backoff exponencial (yfinance
    no es oficial, sufre 429 alrededor de los ~950 tickers seguidos — ver
    ARCHITECTURE_LEAN.md §7). Resumible: se puede parar y reanudar por ticker.
  - requests-cache en disco: nunca se vuelve a pedir el mismo (ticker, rango).

ADVERTENCIA DE VALIDACIÓN — sin ejecutar en vivo (egress bloqueado a
query1/query2.finance.yahoo.com, AUDIT_LEAN.md §1.5). La forma de uso de
`yfinance.download()` sigue la API pública documentada de la librería, pero
no se ha podido correr contra el servidor real desde aquí. Lanzar contra
5-10 tickers conocidos y revisar a mano antes del backfill de 2.500+.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd

from pipeline import config

logger = logging.getLogger(__name__)

BATCH_SIZE = 50           # tickers por lote — margen de sobra bajo el umbral de ~950 reportado
PAUSE_BETWEEN_BATCHES_S = 5
MAX_RETRIES = 4
BACKOFF_BASE_S = 2         # 2, 4, 8, 16 — misma política que el resto del proyecto


def _trading_days_expected(start: date, end: date) -> set[date]:
    """Aproximación de días hábiles de mercado (lunes-viernes, sin festivos).

    Es deliberadamente conservadora: no conocer los festivos de NYSE exactos
    generará algún falso positivo de 'gap' en días festivos reales. Esto es
    aceptable porque el flag es una SEÑAL DE ALERTA, no una verdad absoluta —
    el objetivo es no dejar pasar en silencio un hueco real de deslistado.
    Fase 2 (Norgate/Sharadar, ver DATA_REQUIREMENTS_PHASED.md) trae calendario
    de festivos correcto.
    """
    days = set()
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.add(d)
        d += timedelta(days=1)
    return days


def _download_one_with_retry(ticker: str, start: date, end: date) -> pd.DataFrame | None:
    import yfinance as yf

    for attempt in range(MAX_RETRIES):
        try:
            df = yf.download(
                ticker,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=False,  # crítico: queremos Close crudo Y Adj Close por separado
                progress=False,
                threads=False,
            )
            return df
        except Exception as exc:  # yfinance no tiene una jerarquía de excepciones propia estable
            delay = BACKOFF_BASE_S * (2**attempt)
            logger.warning("Fallo descargando %s (intento %d): %s — esperando %ds", ticker, attempt, exc, delay)
            time.sleep(delay)
    logger.error("Descarga de %s falló tras %d intentos, se omite", ticker, MAX_RETRIES)
    return None


def backfill_tickers(tickers: list[str], start: date, end: date) -> None:
    from pipeline.db.connection import get_connection

    conn = get_connection()
    expected_days = _trading_days_expected(start, end)

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        logger.info("Lote %d-%d de %d tickers", i, i + len(batch), len(tickers))
        for ticker in batch:
            df = _download_one_with_retry(ticker, start, end)
            if df is None or df.empty:
                logger.warning("Sin datos para %s en absoluto — probable deslistado total", ticker)
                _flag_full_gap(conn, ticker, start, end)
                continue
            _store_with_gap_detection(conn, ticker, df, expected_days)
        time.sleep(PAUSE_BETWEEN_BATCHES_S)
    logger.info("Backfill de precios completo para %d tickers", len(tickers))


def _flag_full_gap(conn, ticker: str, start: date, end: date) -> None:
    """Ticker sin ninguna fila devuelta: se registra una única fila centinela
    con survivorship_warning=TRUE en la fecha de inicio, para que quede
    constancia del hueco sin inventar precios."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning)
            VALUES (%s, %s, NULL, NULL, NULL, TRUE)
            ON CONFLICT (ticker, trade_date) DO UPDATE SET survivorship_warning = TRUE
            """,
            (ticker, start),
        )
    conn.commit()


def _store_with_gap_detection(conn, ticker: str, df: pd.DataFrame, expected_days: set[date]) -> None:
    present_days = {d.date() for d in df.index}
    with conn.cursor() as cur:
        for idx, row in df.iterrows():
            d = idx.date()
            close_raw = float(row["Close"])
            adj_close = float(row["Adj Close"])
            adj_factor = adj_close / close_raw if close_raw else None
            cur.execute(
                """
                INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    close_raw = EXCLUDED.close_raw,
                    adj_factor = EXCLUDED.adj_factor,
                    volume = EXCLUDED.volume,
                    captured_at = now()
                """,
                (ticker, d, close_raw, adj_factor, int(row["Volume"])),
            )
        # Días esperados dentro del rango de ESTE ticker que no vinieron en absoluto:
        # se marcan como huecos sin inventar una fila de precio.
        ticker_range_expected = {d for d in expected_days if min(present_days, default=d) <= d <= max(present_days, default=d)}
        missing = ticker_range_expected - present_days
        for d in missing:
            cur.execute(
                """
                INSERT INTO prices (ticker, trade_date, close_raw, adj_factor, volume, survivorship_warning)
                VALUES (%s, %s, NULL, NULL, NULL, TRUE)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET survivorship_warning = TRUE
                """,
                (ticker, d),
            )
        if missing:
            logger.warning("WARNING — posible data gap: %s tiene %d días faltantes dentro de su rango", ticker, len(missing))
    conn.commit()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Backfill de precios yfinance con flags de supervivencia")
    parser.add_argument("--tickers", type=str, required=True, help="Fichero con un ticker por línea, o lista separada por comas")
    parser.add_argument("--start", type=str, default=config.BACKTEST_START)
    parser.add_argument("--end", type=str, default=config.BACKTEST_END)
    args = parser.parse_args()

    from datetime import datetime as _dt
    from pathlib import Path

    if Path(args.tickers).exists():
        ticker_list = [line.strip() for line in Path(args.tickers).read_text().splitlines() if line.strip()]
    else:
        ticker_list = [t.strip() for t in args.tickers.split(",")]

    backfill_tickers(
        ticker_list,
        _dt.strptime(args.start, "%Y-%m-%d").date(),
        _dt.strptime(args.end, "%Y-%m-%d").date(),
    )
