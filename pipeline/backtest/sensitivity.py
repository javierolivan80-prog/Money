"""sensitivity.py — Fase 6 PARTE 5 ("SENSIBILIDAD: ¿qué pasa si...?").

Cada escenario se calcula reprocesando los trades YA SIMULADOS de
portfolio_trades (no una resimulación completa día a día) — es una
aproximación deliberada, documentada aquí en vez de en cada función:

- Comisión +0.1% / spread +0.2%: ambos son un coste de transacción
  adicional al de la vuelta completa (COMMISSION_BPS_ROUND_TRIP en
  portfolio_simulator.py) — se restan directamente de pnl_pct. Exacto, no
  una aproximación (el coste de transacción no depende de qué día se
  disparó el TP/SL, solo de que hubo una entrada y una salida).

- Latencia D+2 en vez de D+1: aproximación. Recalcula el precio de entrada
  al segundo día de negociación tras D0 (en vez del primero) y recalcula
  pnl_pct con ESE precio de entrada contra el MISMO exit_price/exit_date ya
  registrado — no vuelve a correr el día a día completo (que podría hacer
  que un TP/SL se disparase un día distinto con una entrada distinta). Para
  la pregunta que responde esta sección ("¿el resultado depende
  frágilmente de lograr entrar exactamente en D+1?"), esta aproximación ya
  es informativa; una resimulación completa sería más precisa pero mucho
  más cara, y el spec solo pide una tabla de escenarios, no una segunda
  fuente de verdad.

- VIX +50%: una resimulación estocástica de camino de precios bajo un
  choque de volatilidad hipotético queda fuera de alcance (este proyecto no
  tiene un modelo de precios, solo el histórico observado). En su lugar se
  usa la varianza YA OBSERVADA de vix_d0 entre eventos históricos como
  proxy de dependencia de régimen: se parte la muestra en
  alto-VIX-en-la-entrada / bajo-VIX-en-la-entrada (mediana) y se reportan
  las métricas de cada mitad — la misma pregunta que PARTE 4 pide bajo
  "regime dependency", resuelta una sola vez y reportada en ambos sitios.

- Confidence -20%: recorta a los trades cuya confidence*0.8 seguiría
  pasando el único filtro de abstención que depende de confidence de forma
  monótona decreciente — el piso CONFIDENCE_FLOOR de abstention_engine.py
  (bajar confidence nunca activa la regla de "contradicción", que exige
  confidence ALTA — ver abstention_engine.py:_is_contradictory; se
  documenta para no reimplicar por error esa regla aquí).
"""
from __future__ import annotations

from datetime import date

import numpy as np

from pipeline.analyze.abstention_engine import CONFIDENCE_FLOOR
from pipeline.backtest.portfolio_metrics import compute_equity_metrics, compute_trade_metrics
from pipeline.backtest.portfolio_simulator import COMMISSION_BPS_ROUND_TRIP, gain_pct

SPREAD_SENSITIVITY_BPS = 20.0  # +0.2%
COMMISSION_SENSITIVITY_BPS = 10.0  # +0.1%
CONFIDENCE_HAIRCUT_FRACTION = 0.20  # -20%


def _fetch_trades_with_context(conn, version: str, run_batch_tag: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pt.*, e.ticker, e.event_class
            FROM portfolio_trades pt
            JOIN events e ON e.event_id = pt.event_id
            WHERE pt.version = %s AND pt.run_batch_tag = %s
            ORDER BY pt.entry_date
            """,
            (version, run_batch_tag),
        )
        return cur.fetchall()


def apply_extra_cost_bps(trades: list[dict], extra_bps: float) -> list[dict]:
    """Resta extra_bps/100 puntos porcentuales de pnl_pct de cada trade —
    mismo mecanismo con el que COMMISSION_BPS_ROUND_TRIP ya se aplica en
    consolidate_trade_record. pnl_abs se recalcula proporcionalmente para
    que la curva de equity reconstruida en compute_equity_impact sea
    consistente."""
    adjusted = []
    for t in trades:
        new_pnl_pct = float(t["pnl_pct"]) - extra_bps / 100
        size = float(t["position_size_dollars"])
        adjusted.append({**t, "pnl_pct": new_pnl_pct, "pnl_abs": size * (new_pnl_pct / 100)})
    return adjusted


def apply_latency_sensitivity(conn, trades: list[dict]) -> list[dict]:
    """Reprecia la entrada al segundo día de negociación tras d0_close_date
    (D+2) en vez del primero (D+1) — ver docstring del módulo sobre por qué
    esto es una aproximación (mismo exit_date/exit_price)."""
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, d0_close_date FROM events WHERE event_id = ANY(%s)", ([t["event_id"] for t in trades],))
        d0_by_event = {r["event_id"]: r["d0_close_date"] for r in cur.fetchall()}

    ticker_cache: dict[str, dict[date, dict]] = {}
    adjusted = []
    for t in trades:
        ticker = t["ticker"]
        if ticker not in ticker_cache:
            with conn.cursor() as cur:
                cur.execute("SELECT trade_date, open_raw FROM prices WHERE ticker = %s ORDER BY trade_date", (ticker,))
                ticker_cache[ticker] = {r["trade_date"]: r["open_raw"] for r in cur.fetchall()}
        prices = ticker_cache[ticker]
        d0 = d0_by_event.get(t["event_id"])
        future_dates = sorted(d for d in prices if d is not None and d > d0) if d0 else []
        if len(future_dates) < 2 or prices[future_dates[1]] is None:
            continue  # sin un D+2 disponible, no se puede recalcular este trade — se omite
        new_entry_price = float(prices[future_dates[1]])
        actual_move_pct = gain_pct(t["direction"], new_entry_price, float(t["exit_price"]))
        new_pnl_pct = actual_move_pct - COMMISSION_BPS_ROUND_TRIP / 100
        size = float(t["position_size_dollars"])
        adjusted.append({**t, "entry_price": new_entry_price, "pnl_pct": new_pnl_pct, "pnl_abs": size * (new_pnl_pct / 100)})
    return adjusted


def apply_confidence_haircut(trades: list[dict], haircut_fraction: float = CONFIDENCE_HAIRCUT_FRACTION) -> list[dict]:
    """Excluye los trades cuya confidence, reducida en haircut_fraction,
    caería por debajo de CONFIDENCE_FLOOR (la única regla de abstención de
    abstention_engine.py que una confidence MENOR puede activar — ver
    docstring del módulo)."""
    kept = []
    for t in trades:
        adjusted_confidence = float(t["confidence"]) * (1 - haircut_fraction)
        if adjusted_confidence >= CONFIDENCE_FLOOR:
            kept.append(t)
    return kept


def split_by_vix_regime(conn, trades: list[dict]) -> dict[str, list[dict]]:
    """Divide los trades en alto-VIX / bajo-VIX en la entrada (mediana de
    vix_d0 entre los propios trades) — proxy de "¿funciona en regímenes de
    volatilidad distintos?" (PARTE 4 del spec) y de "¿qué pasa si VIX sube
    50%?" (PARTE 5) a la vez, ver docstring del módulo."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_id, vix_d0 FROM event_enrichment WHERE event_id = ANY(%s)",
            ([t["event_id"] for t in trades],),
        )
        vix_by_event = {r["event_id"]: float(r["vix_d0"]) for r in cur.fetchall() if r["vix_d0"] is not None}

    with_vix = [t for t in trades if t["event_id"] in vix_by_event]
    if len(with_vix) < 4:
        return {"high_vix": [], "low_vix": [], "n_missing_vix": len(trades) - len(with_vix)}

    median_vix = float(np.median([vix_by_event[t["event_id"]] for t in with_vix]))
    high = [t for t in with_vix if vix_by_event[t["event_id"]] >= median_vix]
    low = [t for t in with_vix if vix_by_event[t["event_id"]] < median_vix]
    return {"high_vix": high, "low_vix": low, "median_vix": median_vix, "n_missing_vix": len(trades) - len(with_vix)}


def _rebuild_equity_curve(trades: list[dict], starting_capital: float) -> list[dict]:
    """Curva de equity aproximada a partir de una lista de trades ya
    reprocesada (sin re-simular el día a día): ordena por exit_date y
    acumula pnl_abs — ignora el efecto de posiciones simultáneas sobre el
    tamaño disponible (portfolio_simulator.py sí lo modela para la curva
    oficial); aquí basta para ver la DIRECCIÓN del impacto de cada
    escenario, no para sustituir la curva de equity real."""
    balance = starting_capital
    curve = []
    for t in sorted(trades, key=lambda t: t["exit_date"]):
        balance += float(t["pnl_abs"])
        curve.append({"trade_date": t["exit_date"], "balance": balance})
    return curve


def summarize_scenario(trades: list[dict], starting_capital: float) -> dict:
    trade_metrics = compute_trade_metrics(trades)
    curve = _rebuild_equity_curve(trades, starting_capital)
    equity_metrics = compute_equity_metrics(curve, starting_capital)
    return {"n_trades": trade_metrics["total_trades"], "win_rate": trade_metrics["win_rate"], "total_return": equity_metrics["total_return"]}


def run_sensitivity_analysis(conn, run_batch_tag: str, starting_capital: float = 100_000.0) -> dict:
    """Punto de entrada — corre los 5 escenarios del spec sobre
    CONSERVATIVE y AGGRESSIVE (BALANCED es una mezcla de los dos, se omite
    de la tabla por legibilidad, igual que el spec solo pide esas dos
    columnas) y devuelve la tabla completa."""
    scenarios: dict[str, dict[str, dict]] = {}

    for version in ("CONSERVATIVE", "AGGRESSIVE"):
        trades = _fetch_trades_with_context(conn, version, run_batch_tag)
        baseline = summarize_scenario(trades, starting_capital)

        commission_trades = apply_extra_cost_bps(trades, COMMISSION_SENSITIVITY_BPS)
        spread_trades = apply_extra_cost_bps(trades, SPREAD_SENSITIVITY_BPS)
        latency_trades = apply_latency_sensitivity(conn, trades)
        confidence_trades = apply_confidence_haircut(trades)
        vix_split = split_by_vix_regime(conn, trades)

        scenarios[version] = {
            "baseline": baseline,
            "commission_plus_0.1pct": summarize_scenario(commission_trades, starting_capital),
            "spread_plus_0.2pct": summarize_scenario(spread_trades, starting_capital),
            "latency_d_plus_2": summarize_scenario(latency_trades, starting_capital),
            "confidence_minus_20pct": summarize_scenario(confidence_trades, starting_capital),
            "high_vix_regime": summarize_scenario(vix_split.get("high_vix", []), starting_capital),
            "low_vix_regime": summarize_scenario(vix_split.get("low_vix", []), starting_capital),
        }

    return {"run_batch_tag": run_batch_tag, "scenarios": scenarios}
