"""populate_car_results.py — calcula y guarda CAR para eventos que aún no lo tienen.

Era el hueco señalado en RUNBOOK.md tras la Fase 1 ("el orquestador que
conecta backtest/backtester.py con la tabla events") y se cierra aquí porque
analyze/historical_analogues.py (Fase 2, Etapa 6) depende directamente de que
car_results tenga filas — sin esto, todo impact_estimation de producción
tendría n_analogues=0 permanentemente, no por diseño sino por un paso que
faltaba conectar.

Calcula ambas ventanas (5 y 20 días) pedidas por backtest/backtester.py, para
cada evento con precio + factores suficientes. Un evento sin suficiente
historial (fit_factor_model devuelve None) se salta — no se guarda una fila
con CAR fabricado.
"""
from __future__ import annotations

import logging

import pandas as pd

from pipeline.backtest.backtester import compute_car

logger = logging.getLogger(__name__)

WINDOW_DAYS_LIST = [5, 20]


def _load_ticker_returns(conn, ticker: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, close_raw, adj_factor FROM prices WHERE ticker = %s ORDER BY trade_date",
            (ticker,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").astype({"close_raw": float, "adj_factor": float})
    adj_close = df["close_raw"] * df["adj_factor"].fillna(1.0)
    return pd.DataFrame({"ret": adj_close.pct_change()}).dropna()


def _load_factor_returns(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("SELECT trade_date, mkt_rf, smb, hml, rf FROM fama_french_factors ORDER BY trade_date")
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date").astype(float)


def populate_missing_car_results(conn, limit: int = 1000) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.ticker, e.d0_close_date
            FROM events e
            LEFT JOIN car_results cr ON cr.event_id = e.event_id AND cr.window_days = 20
            WHERE cr.event_id IS NULL
            ORDER BY e.event_id
            LIMIT %s
            """,
            (limit,),
        )
        pending = cur.fetchall()

    if not pending:
        return 0

    factor_returns = _load_factor_returns(conn)
    if factor_returns.empty:
        logger.warning("Sin factores Fama-French cargados — no se puede calcular ningún CAR todavía")
        return 0

    stored = 0
    ticker_cache: dict[str, pd.DataFrame] = {}
    for row in pending:
        ticker = row["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = _load_ticker_returns(conn, ticker)
        event_prices = ticker_cache[ticker]
        if event_prices.empty:
            continue

        for window_days in WINDOW_DAYS_LIST:
            result = compute_car(event_prices, factor_returns, row["d0_close_date"], window_days)
            if result is None:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO car_results (event_id, window_days, car, abnormal_volume_ratio, n_estimation_days)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (event_id, window_days) DO UPDATE SET
                        car = EXCLUDED.car, abnormal_volume_ratio = EXCLUDED.abnormal_volume_ratio,
                        n_estimation_days = EXCLUDED.n_estimation_days, computed_at = now()
                    """,
                    (row["event_id"], window_days, result.car, result.abnormal_volume_ratio, result.n_estimation_days),
                )
            stored += 1
    conn.commit()
    return stored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    conn = get_connection()
    n = populate_missing_car_results(conn)
    print(f"{n} filas de car_results calculadas/actualizadas")
