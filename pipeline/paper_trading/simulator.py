"""simulator.py — Fase 4 del spec del usuario ("Paper Trading simulado"):
simula la ÚLTIMA SEMANA COMPLETA de datos disponibles como si fuera "futura",
event por event, con la misma disciplina anti-look-ahead que el backtest
histórico (portfolio_simulator.py) — pero deliberadamente MÁS SIMPLE en la
mecánica de salida, porque el log que pide el spec (status:
OPEN/CLOSED_TP/CLOSED_SL/CLOSED_TIMEOUT) es de una sola pieza, sin cierres
parciales:

1. SIN trailing-stop de tramos (Aggressive): en el backtest histórico,
   Aggressive cierra en tramos de 30% cada +20% de ganancia
   (portfolio_strategies.py). Aquí eso no cabe en un status de una sola
   pieza, así que se usa un ÚNICO take-profit al primer umbral del trailing
   (+20%, el primer tramo) como estand-in — preserva que Aggressive deja
   correr más que Conservative (+2%), sin inventar un tercer parámetro que
   el spec no pide. Documentado aquí, no en el dato: es una simplificación
   DELIBERADA de esta simulación de una semana, no una corrección del
   backtest histórico completo (que sigue usando los tramos reales).

2. SIN sizing en dólares: el log del spec no pide position_size ni P&L en $,
   solo pnl_pct — igual que el dashboard de la Fase 1 ya usaba
   cumulative_return_pct en vez de balance. Se reutiliza OpenPosition con
   position_size_dollars=0 solo para poder reutilizar consolidate_trade_record
   (que sí calcula pnl_pct con la comisión de 10 bps ya aplicada — MISMA
   comisión que el backtest histórico, a propósito: para que "¿el paper
   trading confirma el backtest?" compare manzanas con manzanas).

3. Estado OPEN persistente: a diferencia del backtest histórico (que fuerza
   el cierre de todo al final del panel de precios porque ahí termina la
   "historia"), aquí si TP/SL no dispara y todavía no hay datos de precio
   hasta el fin de semana simulado, la posición queda OPEN — es lo correcto
   ("todavía no lo sabemos"), no un caso sin cubrir. Si SÍ hay datos hasta
   el fin de semana y no disparó nada, se cierra CLOSED_TIMEOUT ahí.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from pipeline.backtest.portfolio_simulator import (
    OpenPosition,
    compute_tp_sl_prices,
    consolidate_trade_record,
    step_position_forward,
)
from pipeline.backtest.portfolio_strategies import (
    STRATEGIES,
    classify_balanced_execution_style,
)

logger = logging.getLogger(__name__)

VERSIONS = ("CONSERVATIVE", "AGGRESSIVE", "BALANCED")

# Mapeo entre los motivos de cierre de step_position_forward (compartidos con
# el backtest histórico) y el vocabulario del log de paper trading del spec.
_EXIT_REASON_TO_STATUS = {
    "STOP_LOSS": "CLOSED_SL",
    "TAKE_PROFIT": "CLOSED_TP",
    "MAX_HOLDING": "CLOSED_TIMEOUT",
}
_EXIT_REASON_TO_PAPER_LABEL = {
    "STOP_LOSS": "STOP_LOSS",
    "TAKE_PROFIT": "TAKE_PROFIT",
    "MAX_HOLDING": "TIMEOUT",
}


def select_simulation_week(as_of: date | None = None) -> tuple[date, date]:
    """"Selecciona la ÚLTIMA SEMANA COMPLETA de datos disponibles" — se
    interpreta como la última semana Lunes-Viernes ya terminada por completo
    antes de `as_of` (hoy por defecto). Si as_of es un martes, la semana en
    curso NO está completa (solo lleva lunes+martes) — se usa la semana
    anterior. Es una función pura (sin BD): qué eventos hay de verdad en esa
    ventana lo decide fetch_events_for_week."""
    as_of = as_of or date.today()
    monday_this_week = as_of - timedelta(days=as_of.weekday())
    week_end = monday_this_week - timedelta(days=3)  # viernes de la semana anterior
    week_start = week_end - timedelta(days=4)  # lunes de esa misma semana
    return week_start, week_end


def fetch_events_for_week(conn, version: str, week_start: date, week_end: date) -> list[dict]:
    """Eventos con trade_decision != NO_TRADE para `version`, cuyo
    d0_close_date cae dentro de [week_start, week_end] — mismo shape de
    columnas que portfolio_simulator.fetch_events_for_version, con el
    filtro de semana añadido."""
    assert version in VERSIONS
    trade_decision_col = f"trade_decision_{version.lower()}"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT e.event_id, e.ticker, e.d0_close_date,
                   ea.{trade_decision_col} AS trade_decision,
                   ea.ev_conservative, ea.ev_aggressive, ea.ev_balanced,
                   ea.confidence_in_conviction AS confidence,
                   ea.net_conviction AS prediction
            FROM events e
            JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE ea.{trade_decision_col} != 'NO_TRADE'
              AND e.d0_close_date BETWEEN %s AND %s
            ORDER BY e.d0_close_date
            """,
            (week_start, week_end),
        )
        return cur.fetchall()


def fetch_all_events_for_week(conn, week_start: date, week_end: date) -> list[dict]:
    """TODOS los eventos analizados de la semana, con o sin trade_decision —
    para la comparación predicción-vs-real (spec: "Guardar esto para TODOS
    los eventos de la semana"), que evalúa la calidad de la señal cruda
    independientemente de si pasó el filtro de EV/confianza para operarse."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.ticker, e.event_class, e.d0_close_date,
                   ea.ev_conservative, ea.ev_aggressive, ea.ev_balanced,
                   ea.confidence_in_conviction AS confidence,
                   ea.net_conviction AS prediction,
                   ea.trade_decision_conservative, ea.trade_decision_aggressive,
                   ea.trade_decision_balanced
            FROM events e
            JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE e.d0_close_date BETWEEN %s AND %s
            ORDER BY e.d0_close_date
            """,
            (week_start, week_end),
        )
        return cur.fetchall()


def load_ticker_prices(conn, ticker: str) -> dict[date, dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, open_raw, high_raw, low_raw, close_raw "
            "FROM prices WHERE ticker = %s ORDER BY trade_date",
            (ticker,),
        )
        return {r["trade_date"]: r for r in cur.fetchall()}


def _paper_tp_sl_pct(style: str) -> tuple[float | None, float]:
    """take_profit_pct efectivo para paper trading — ver nota 1 del
    docstring del módulo sobre el estand-in de Aggressive."""
    config = STRATEGIES[style]
    if config.take_profit_pct is not None:
        return config.take_profit_pct, config.stop_loss_pct
    first_tier_threshold = config.trailing_stop_tiers[0][0]
    return first_tier_threshold, config.stop_loss_pct


def _build_paper_plan(ev: dict, version: str) -> dict:
    """Misma lógica de dirección/estilo/ev que
    portfolio_simulator._build_entry_plan, sin depender de esa función
    porque target_date aquí es fijo (fin de semana simulada), no
    config.holding_period_max_days."""
    direction = "LONG" if float(ev["prediction"]) > 0 else "SHORT"
    if version == "BALANCED":
        style = classify_balanced_execution_style(float(ev["confidence"]), float(ev["ev_conservative"]), float(ev["ev_aggressive"]))
        ev_value = float(ev["ev_conservative"]) if style == "CONSERVATIVE" else float(ev["ev_aggressive"])
    else:
        style = version
        ev_value = float(ev["ev_conservative"] if version == "CONSERVATIVE" else ev["ev_aggressive"])
    return {
        "event_id": ev["event_id"],
        "ticker": ev["ticker"],
        "direction": direction,
        "execution_style": style,
        "ev": ev_value,
        "confidence": float(ev["confidence"]),
        "prediction": float(ev["prediction"]),
    }


def simulate_single_paper_trade(ticker_prices: dict[date, dict], plan: dict, entry_date: date, entry_price: float, week_end: date) -> dict:
    """Simula UNA posición desde su entrada (D+1, ya calculada por el
    caller) hasta que dispara TP/SL o llega el fin de semana simulada — lo
    que pase primero. Devuelve un dict con 'status' y, si se cerró, los
    campos de salida. Pura respecto a BD (recibe los precios ya cargados)."""
    take_profit_pct, stop_loss_pct = _paper_tp_sl_pct(plan["execution_style"])
    tp_price, sl_price = compute_tp_sl_prices(plan["direction"], entry_price, take_profit_pct, stop_loss_pct)

    position = OpenPosition(
        event_id=plan["event_id"],
        version=plan["version"],
        execution_style=plan["execution_style"],
        direction=plan["direction"],
        ticker=plan["ticker"],
        entry_date=entry_date,
        entry_price=entry_price,
        position_size_pct=0.0,
        position_size_dollars=0.0,
        target_date=week_end,
        take_profit_price=tp_price,
        stop_loss_price=sl_price,
        trailing_tiers=None,  # ver nota 1 del docstring del módulo
        confidence=plan["confidence"],
        ev=plan["ev"],
        prediction=plan["prediction"],
        had_survivorship_warning=False,
    )

    trading_dates_in_week = sorted(d for d in ticker_prices if entry_date < d <= week_end)
    for d in trading_dates_in_week:
        bar = ticker_prices[d]
        if bar["high_raw"] is None or bar["low_raw"] is None or bar["close_raw"] is None:
            continue
        step_position_forward(position, float(bar["high_raw"]), float(bar["low_raw"]), float(bar["close_raw"]), d)
        if position.remaining_fraction <= 1e-9:
            break

    known_through = max(ticker_prices.keys()) if ticker_prices else None
    if position.remaining_fraction > 1e-9:
        if known_through is not None and known_through >= week_end and trading_dates_in_week:
            # Hay datos hasta (al menos) el fin de semana y no disparó nada:
            # se cierra a mercado en el último día de negociación <= week_end.
            close_date = trading_dates_in_week[-1]
            close_price = float(ticker_prices[close_date]["close_raw"])
            position.closes.append((close_date, 1.0, close_price, "MAX_HOLDING"))
            position.remaining_fraction = 0.0
        else:
            # Todavía no sabemos — la semana simulada no ha "terminado" en
            # términos de datos de precio disponibles.
            return {
                "event_id": plan["event_id"],
                "ticker": plan["ticker"],
                "direction": plan["direction"],
                "entry_date": entry_date,
                "entry_price": entry_price,
                "status": "OPEN",
                "exit_date": None,
                "exit_price": None,
                "exit_reason": None,
                "pnl_pct": None,
                "confidence": plan["confidence"],
                "ev": plan["ev"],
                "prediction": plan["prediction"],
            }

    record = consolidate_trade_record(position)
    return {
        "event_id": plan["event_id"],
        "ticker": plan["ticker"],
        "direction": plan["direction"],
        "entry_date": entry_date,
        "entry_price": entry_price,
        "status": _EXIT_REASON_TO_STATUS[record["exit_reason"]],
        "exit_date": record["exit_date"],
        "exit_price": record["exit_price"],
        "exit_reason": _EXIT_REASON_TO_PAPER_LABEL[record["exit_reason"]],
        "pnl_pct": record["pnl_pct"],
        "confidence": plan["confidence"],
        "ev": plan["ev"],
        "prediction": plan["prediction"],
    }


def simulate_paper_trading_week(conn, version: str, week_start: date, week_end: date, run_batch_tag: str) -> list[dict]:
    """Punto de entrada por versión — para cada evento de la semana con
    trade_decision != NO_TRADE, calcula la entrada D+1 real (con los datos
    de precio del propio ticker) y simula la posición hasta TP/SL o fin de
    semana. Eventos sin datos de precio suficientes para calcular siquiera
    la entrada se omiten con un warning (mismo criterio que
    portfolio_simulator._build_entry_plan)."""
    assert version in VERSIONS
    events = fetch_events_for_week(conn, version, week_start, week_end)
    if not events:
        logger.warning("Sin eventos con trade_decision para %s en semana %s-%s", version, week_start, week_end)
        return []

    ticker_cache: dict[str, dict[date, dict]] = {}
    results = []
    for ev in events:
        ticker = ev["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = load_ticker_prices(conn, ticker)
        prices = ticker_cache[ticker]

        future_dates = sorted(d for d in prices if d > ev["d0_close_date"])
        if not future_dates:
            logger.warning("Evento %d (%s): sin precios posteriores a D0, se omite del paper trading", ev["event_id"], ticker)
            continue
        entry_date = future_dates[0]
        if entry_date > week_end:
            logger.warning("Evento %d (%s): D+1=%s cae fuera de la semana simulada (%s), se omite", ev["event_id"], ticker, entry_date, week_end)
            continue
        entry_bar = prices[entry_date]
        if entry_bar["open_raw"] is None:
            logger.warning("Evento %d (%s): sin precio de apertura en la entrada, se omite", ev["event_id"], ticker)
            continue

        plan = _build_paper_plan(ev, version)
        plan["version"] = version
        result = simulate_single_paper_trade(prices, plan, entry_date, float(entry_bar["open_raw"]), week_end)
        result["version"] = version
        result["run_batch_tag"] = run_batch_tag
        result["week_start"] = week_start
        result["week_end"] = week_end
        results.append(result)

    _store_paper_trades(conn, results)
    return results


def _store_paper_trades(conn, trades: list[dict]) -> None:
    if not trades:
        return
    with conn.cursor() as cur:
        for t in trades:
            cur.execute(
                """
                INSERT INTO paper_trades (
                    run_batch_tag, week_start, week_end, event_id, version, direction,
                    entry_date, entry_price, exit_date, exit_price, exit_reason, status,
                    pnl_pct, confidence, ev, prediction
                ) VALUES (
                    %(run_batch_tag)s, %(week_start)s, %(week_end)s, %(event_id)s, %(version)s, %(direction)s,
                    %(entry_date)s, %(entry_price)s, %(exit_date)s, %(exit_price)s, %(exit_reason)s, %(status)s,
                    %(pnl_pct)s, %(confidence)s, %(ev)s, %(prediction)s
                )
                ON CONFLICT (event_id, version, run_batch_tag) DO UPDATE SET
                    exit_date = EXCLUDED.exit_date, exit_price = EXCLUDED.exit_price,
                    exit_reason = EXCLUDED.exit_reason, status = EXCLUDED.status,
                    pnl_pct = EXCLUDED.pnl_pct, updated_at = now()
                """,
                t,
            )
    conn.commit()
