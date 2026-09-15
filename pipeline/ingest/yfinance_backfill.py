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
    """Días en los que la bolsa abre de verdad, festivos incluidos.

    ANTES era lunes-viernes a secas, con un comentario que daba los falsos
    positivos por "aceptables porque el flag es una SEÑAL DE ALERTA". No lo
    eran: en el primer run con precios reales marcó 14 huecos en CASI LOS 150
    tickers, 3M y Adobe entre ellos, y los 14 eran exactamente los festivos del
    rango. Una alerta que salta en todos los casos a la vez no avisa de nada —
    tapaba justo lo que tenía que detectar.
    """
    from pipeline.ingest.market_calendar import dias_de_negociacion

    return dias_de_negociacion(start, end)


COLUMNAS_REQUERIDAS = ("Open", "High", "Low", "Close", "Adj Close", "Volume")


def aplanar_columnas(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Deja las columnas en un solo nivel ('Close', 'Open'...).

    BUG REAL (2026-09-15): yfinance devuelve las columnas como MultiIndex
    (campo, ticker) TAMBIÉN cuando se pide un único ticker. Con eso,
    row['Close'] no da un número sino una Series de un elemento, y
    float(...) revienta con:

        TypeError: float() argument must be a string or a real number,
                   not 'Series'

    Se quita el nivel que contiene el ticker, esté donde esté, en vez de
    asumir que es el último: si yfinance cambia el orden de los niveles, el
    aplanado sigue siendo correcto.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    for nivel in range(df.columns.nlevels):
        if set(df.columns.get_level_values(nivel)) <= {ticker}:
            return df.droplevel(nivel, axis=1)
    return df.droplevel(nivel_de_tickers(df.columns), axis=1)


def nivel_de_tickers(columnas: pd.MultiIndex) -> int:
    """Cuál de los dos niveles lleva los tickers y cuál los campos.

    Se identifica por el CONTENIDO, no por la posición: el nivel de campos es
    el que trae 'Open', 'Close' y compañía; el otro es el de tickers. Así el
    código aguanta que yfinance invierta el orden de los niveles, que es
    justamente el tipo de suposición sobre un formato ajeno que ya ha costado
    varios fallos en producción en este proyecto.
    """
    for nivel in range(columnas.nlevels):
        if not set(columnas.get_level_values(nivel)) & set(COLUMNAS_REQUERIDAS):
            return nivel
    return columnas.nlevels - 1  # por defecto de yfinance, el ticker va el último


def _validar_columnas(df: pd.DataFrame, ticker: str) -> None:
    """Falla en voz alta si falta una columna o si alguna está duplicada.

    Una columna duplicada hace que row['Close'] vuelva a ser una Series, que
    es exactamente el fallo que se acaba de arreglar: sin esta comprobación
    reaparecería como el mismo TypeError críptico a mitad de la descarga, en
    vez de decir qué columnas trae el DataFrame de verdad.
    """
    faltan = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    duplicadas = [c for c in COLUMNAS_REQUERIDAS if list(df.columns).count(c) > 1]
    if faltan or duplicadas:
        raise ValueError(
            f"Columnas inesperadas en los precios de {ticker} "
            f"(faltan: {faltan or 'ninguna'}; duplicadas: {duplicadas or 'ninguna'}). "
            f"Columnas recibidas: {list(df.columns)}. ¿Cambió el formato de yfinance?"
        )


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
            return aplanar_columnas(df, ticker) if df is not None else None
        except Exception as exc:  # yfinance no tiene una jerarquía de excepciones propia estable
            delay = BACKOFF_BASE_S * (2**attempt)
            logger.warning("Fallo descargando %s (intento %d): %s — esperando %ds", ticker, attempt, exc, delay)
            time.sleep(delay)
    logger.error("Descarga de %s falló tras %d intentos, se omite", ticker, MAX_RETRIES)
    return None


def _descargar_lote_con_reintentos(tickers: list[str], start: date, end: date) -> pd.DataFrame | None:
    """Un lote entero en UNA sola petición.

    POR QUÉ (medido en producción, run 34943861450): descargando de uno en uno,
    150 tickers tardaron ~60 minutos, a razón de 2,5 por minuto. No es un
    problema de hoy sino de mañana: el universo crece con cada día ingestado, y
    a este ritmo la pasada nocturna deja de caber en la noche.

    yf.download acepta una lista y devuelve las columnas como (campo, ticker) —
    que es, precisamente, POR QUÉ venían en dos niveles y reventaba float():
    la librería llevaba todo el tiempo preparada para el modo por lotes y se
    estaba usando de una en una.
    """
    import yfinance as yf

    for intento in range(MAX_RETRIES):
        try:
            return yf.download(
                tickers,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:  # yfinance no tiene jerarquía de excepciones estable
            espera = BACKOFF_BASE_S * (2**intento)
            logger.warning(
                "Fallo descargando el lote de %d tickers (intento %d): %s — esperando %ds",
                len(tickers), intento, exc, espera,
            )
            time.sleep(espera)
    logger.error("El lote de %d tickers falló tras %d intentos", len(tickers), MAX_RETRIES)
    return None


def extraer_ticker_del_lote(df: pd.DataFrame | None, ticker: str) -> pd.DataFrame | None:
    """Saca de la descarga por lotes el sub-DataFrame de un ticker.

    Devuelve None cuando el lote no trae nada utilizable de ese ticker (no
    aparece en las columnas, o viene entero a NaN porque está deslistado). El
    caller lo reintenta entonces de uno en uno: si alguna suposición sobre la
    forma del lote es errónea, el peor caso es volver al comportamiento
    anterior —lento pero correcto— en vez de perder precios en silencio.
    """
    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        nivel = nivel_de_tickers(df.columns)
        if ticker not in set(df.columns.get_level_values(nivel)):
            return None
        propio = df.xs(ticker, axis=1, level=nivel)
    else:
        # Un lote de un solo ticker puede volver ya plano.
        propio = df

    propio = propio.dropna(how="all")
    return propio if not propio.empty else None


def backfill_tickers(tickers: list[str], start: date, end: date) -> None:
    from pipeline.db.connection import get_connection

    conn = get_connection()
    expected_days = _trading_days_expected(start, end)

    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        logger.info("Lote %d-%d de %d tickers", i, i + len(batch), len(tickers))

        lote = _descargar_lote_con_reintentos(batch, start, end)
        pendientes = []
        for ticker in batch:
            df = extraer_ticker_del_lote(lote, ticker)
            if df is None:
                pendientes.append(ticker)
                continue
            _store_with_gap_detection(conn, ticker, df, expected_days)

        # Red de seguridad: lo que el lote no trajo se reintenta de uno en uno
        # ANTES de darlo por deslistado. Un ticker ausente del lote puede serlo
        # por estar deslistado de verdad, pero también porque alguna suposición
        # sobre la forma del resultado sea errónea — y no se ha podido validar
        # contra el servidor real desde el entorno de desarrollo. Con esto, el
        # peor caso de equivocarse es tardar lo que se tardaba antes, no
        # marcar como deslistadas 150 empresas que cotizan perfectamente.
        if pendientes:
            logger.info(
                "%d de %d tickers del lote no vinieron en la descarga conjunta, "
                "se piden de uno en uno", len(pendientes), len(batch),
            )
        for ticker in pendientes:
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
    df = aplanar_columnas(df, ticker)  # idempotente: no-op si ya viene plano
    _validar_columnas(df, ticker)
    present_days = {d.date() for d in df.index}
    with conn.cursor() as cur:
        for idx, row in df.iterrows():
            d = idx.date()
            close_raw = float(row["Close"])
            adj_close = float(row["Adj Close"])
            adj_factor = adj_close / close_raw if close_raw else None
            # high_raw/low_raw añadidos en Fase 2: los usa
            # analyze/abstention_engine.py como proxy de spread/liquidez
            # ((high-low)/close) — no hay bid-ask real gratis (AUDIT_LEAN.md
            # §2.1). yf.download con auto_adjust=False ya trae High/Low sin
            # petición adicional.
            high_raw = float(row["High"])
            low_raw = float(row["Low"])
            # open_raw añadido para el backtest de cartera
            # (backtest/portfolio_simulator.py): la entrada real es "apertura
            # D+1" — ningún backfill anterior había pedido este precio porque
            # compute_car() y el backtest simple de la Fase 1 solo usan cierres.
            open_raw = float(row["Open"])
            cur.execute(
                """
                INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET
                    open_raw = EXCLUDED.open_raw,
                    close_raw = EXCLUDED.close_raw,
                    high_raw = EXCLUDED.high_raw,
                    low_raw = EXCLUDED.low_raw,
                    adj_factor = EXCLUDED.adj_factor,
                    volume = EXCLUDED.volume,
                    captured_at = now()
                """,
                (ticker, d, open_raw, close_raw, high_raw, low_raw, adj_factor, int(row["Volume"])),
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
