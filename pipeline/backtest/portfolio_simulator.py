"""portfolio_simulator.py — bucle diario de gestión de posiciones (spec:
"Backtesting loop"), sin look-ahead.

DISCIPLINA ANTI-LOOK-AHEAD (la misma que backtest_runs en la Fase 1, pero
aplicada día a día en vez de en una sola ventana fija):
  - T_decisión = cierre de D0 (evento ya público, ver events.d0_close_date).
  - T_entrada = APERTURA de D+1, nunca D0. entry_price viene de prices.open_raw
    del primer día de negociación estrictamente posterior a d0_close_date.
  - Cada día del bucle solo usa el high/low/close de ESE día — nunca se
    consulta un precio de una fecha posterior a la que se está evaluando.
  - target_date (fin de holding period) se cuenta en DÍAS DE NEGOCIACIÓN
    reales del propio ticker, no días de calendario.

ORDEN DE PRIORIDAD EN UN MISMO DÍA (decisión de diseño, no está en el spec):
si el low/high de un día cruza TANTO el stop-loss COMO el take-profit (gap
grande), se asume que el stop-loss se ejecutó primero — es la convención
estándar y conservadora en backtesting (nunca asumir el mejor caso posible
cuando el orden intradía real es desconocido). Los tramos de trailing stop
(Aggressive) pueden disparar varios el mismo día si hay un gap; se procesan
en orden de umbral ascendente.

CONSOLIDACIÓN: una posición con cierres parciales por trailing stop se
guarda como UNA fila en portfolio_trades (el spec pide un registro por
trade con un solo entry/exit) — exit_price y pnl son el promedio ponderado
de los tramos; exit_reason es 'TRAILING_STOP' si CUALQUIER tramo disparó,
aunque el resto se cerrara por max_holding (ver schema.sql).

COMISIONES: 10 bps por vuelta completa (entrada+salida), constante y
documentada — no es un barrido de sensibilidad como el slippage de la Fase 1
(ARCHITECTURE_LEAN.md T7); aquí es un único supuesto fijo para poder generar
la curva de equity. Ajustable en COMMISSION_BPS_ROUND_TRIP.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from pipeline.backtest.portfolio_strategies import (
    BALANCED_MAX_CONCURRENT,
    STRATEGIES,
    classify_balanced_execution_style,
    compute_balanced_position_size_pct,
    compute_position_size_pct,
)

logger = logging.getLogger(__name__)

COMMISSION_BPS_ROUND_TRIP = 10.0  # 0.10%, ver docstring del módulo


def gain_pct(direction: str, entry_price: float, price: float) -> float:
    """Retorno %, con signo, del SUBYACENTE — misma convención que
    backtest/backtester.py:compute_trade_return (Fase 1), reutilizada aquí
    para que LONG/SHORT se calculen igual en todo el proyecto."""
    if direction == "LONG":
        return (price - entry_price) / entry_price * 100
    return (entry_price - price) / entry_price * 100


def compute_tp_sl_prices(direction: str, entry_price: float, take_profit_pct: float | None, stop_loss_pct: float) -> tuple[float | None, float]:
    if direction == "LONG":
        tp = entry_price * (1 + take_profit_pct / 100) if take_profit_pct is not None else None
        sl = entry_price * (1 - stop_loss_pct / 100)
    else:
        tp = entry_price * (1 - take_profit_pct / 100) if take_profit_pct is not None else None
        sl = entry_price * (1 + stop_loss_pct / 100)
    return tp, sl


@dataclass
class OpenPosition:
    event_id: int
    version: str
    execution_style: str
    direction: str
    ticker: str
    entry_date: date
    entry_price: float
    position_size_pct: float
    position_size_dollars: float
    target_date: date
    take_profit_price: float | None
    stop_loss_price: float
    trailing_tiers: tuple[tuple[float, float], ...] | None
    confidence: float
    ev: float
    prediction: float
    had_survivorship_warning: bool
    remaining_fraction: float = 1.0
    tiers_hit: frozenset = field(default_factory=frozenset)
    used_trailing_stop: bool = False
    closes: list[tuple[date, float, float, str]] = field(default_factory=list)  # (fecha, fracción, precio, motivo)


def step_position_forward(position: OpenPosition, high: float, low: float, close: float, trade_date: date) -> OpenPosition:
    """Aplica un día de precios a una posición abierta, mutando su estado
    (remaining_fraction, tiers_hit, closes) y devolviéndola. Pura respecto a
    I/O — no toca la base de datos."""
    if position.remaining_fraction <= 1e-9:
        return position  # ya cerrada del todo, nada que hacer (defensivo)

    # 1) Stop-loss — máxima prioridad, ver docstring del módulo.
    sl_triggered = (low <= position.stop_loss_price) if position.direction == "LONG" else (high >= position.stop_loss_price)
    if sl_triggered:
        position.closes.append((trade_date, position.remaining_fraction, position.stop_loss_price, "STOP_LOSS"))
        position.remaining_fraction = 0.0
        return position

    # 2) Take-profit fijo (Conservative; None para Aggressive, que usa trailing).
    if position.take_profit_price is not None:
        tp_triggered = (high >= position.take_profit_price) if position.direction == "LONG" else (low <= position.take_profit_price)
        if tp_triggered:
            position.closes.append((trade_date, position.remaining_fraction, position.take_profit_price, "TAKE_PROFIT"))
            position.remaining_fraction = 0.0
            return position

    # 3) Trailing stop tiers (Aggressive) — pueden dispararse varios el mismo día.
    if position.trailing_tiers:
        best_price_today = high if position.direction == "LONG" else low
        today_gain = gain_pct(position.direction, position.entry_price, best_price_today)
        for threshold, fraction in position.trailing_tiers:
            if threshold in position.tiers_hit:
                continue
            if today_gain >= threshold:
                tier_price = (
                    position.entry_price * (1 + threshold / 100)
                    if position.direction == "LONG"
                    else position.entry_price * (1 - threshold / 100)
                )
                actual_fraction = min(fraction, position.remaining_fraction)
                position.closes.append((trade_date, actual_fraction, tier_price, "TRAILING_STOP"))
                position.remaining_fraction -= actual_fraction
                position.tiers_hit = position.tiers_hit | {threshold}
                position.used_trailing_stop = True
                if position.remaining_fraction <= 1e-9:
                    return position

    # 4) Fin del holding period — cierra lo que quede a mercado.
    if position.remaining_fraction > 1e-9 and trade_date >= position.target_date:
        position.closes.append((trade_date, position.remaining_fraction, close, "MAX_HOLDING"))
        position.remaining_fraction = 0.0

    return position


def compute_position_mtm_dollars(position: OpenPosition, current_price: float) -> float:
    """Valor a mercado de una posición, en $, incluyendo el caso de cierres
    parciales ya realizados (trailing stop a mitad de camino): cada tramo ya
    cerrado se valora a SU PROPIO precio de cierre (ganancia ya asegurada, no
    sujeta al precio de hoy); lo que queda abierto se valora al precio de
    hoy. Sin esto, la curva de equity durante los días entre tramos de un
    trailing stop trataría el 100% del tamaño original como expuesto al
    precio actual, sobreestimando el riesgo de lo que ya se aseguró."""
    realized = 0.0
    for _, fraction, price, _ in position.closes:
        move = gain_pct(position.direction, position.entry_price, price)
        realized += position.position_size_dollars * fraction * (1 + move / 100)
    if position.remaining_fraction > 1e-9:
        move = gain_pct(position.direction, position.entry_price, current_price)
        realized += position.position_size_dollars * position.remaining_fraction * (1 + move / 100)
    return realized


def consolidate_trade_record(position: OpenPosition) -> dict:
    """Colapsa una posición TOTALMENTE cerrada (remaining_fraction==0) en el
    registro único que pide el spec — ver docstring del módulo sobre la
    consolidación de cierres parciales."""
    assert position.remaining_fraction <= 1e-9, "consolidate_trade_record llamado sobre una posición aún abierta"
    assert position.closes, "una posición cerrada debe tener al menos un cierre registrado"

    total_fraction = sum(f for _, f, _, _ in position.closes)
    weighted_exit_price = sum(f * p for _, f, p, _ in position.closes) / total_fraction
    final_exit_date = position.closes[-1][0]
    exit_reason = "TRAILING_STOP" if position.used_trailing_stop else position.closes[-1][3]

    assert final_exit_date > position.entry_date, "VIOLACIÓN ANTI-LOOK-AHEAD: exit_date no es posterior a entry_date"

    actual_move_pct = gain_pct(position.direction, position.entry_price, weighted_exit_price)
    commission_pct = COMMISSION_BPS_ROUND_TRIP / 100
    pnl_pct = actual_move_pct - commission_pct
    pnl_abs = position.position_size_dollars * (pnl_pct / 100)

    return {
        "event_id": position.event_id,
        "version": position.version,
        "execution_style": position.execution_style,
        "direction": position.direction,
        "entry_date": position.entry_date,
        "entry_price": position.entry_price,
        "exit_date": final_exit_date,
        "exit_price": weighted_exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_pct,
        "pnl_abs": pnl_abs,
        "position_size_pct": position.position_size_pct,
        "position_size_dollars": position.position_size_dollars,
        "confidence": position.confidence,
        "ev": position.ev,
        "prediction": position.prediction,
        "actual_move_pct": actual_move_pct,
        "had_survivorship_warning": position.had_survivorship_warning,
    }


def open_position(
    event_id: int,
    version: str,
    execution_style: str,
    direction: str,
    ticker: str,
    entry_date: date,
    entry_price: float,
    target_date: date,
    balance_for_sizing: float,
    confidence: float,
    ev: float,
    prediction: float,
    had_survivorship_warning: bool,
) -> OpenPosition:
    """Construye una OpenPosition con el sizing y los umbrales TP/SL/trailing
    de execution_style (STRATEGIES[execution_style] — para BALANCED, ver
    compute_balanced_position_size_pct en vez de compute_position_size_pct)."""
    config = STRATEGIES[execution_style]
    if version == "BALANCED":
        size_pct = compute_balanced_position_size_pct(execution_style)
    else:
        size_pct = compute_position_size_pct(confidence, config)
    size_dollars = balance_for_sizing * (size_pct / 100)
    tp_price, sl_price = compute_tp_sl_prices(direction, entry_price, config.take_profit_pct, config.stop_loss_pct)

    return OpenPosition(
        event_id=event_id,
        version=version,
        execution_style=execution_style,
        direction=direction,
        ticker=ticker,
        entry_date=entry_date,
        entry_price=entry_price,
        position_size_pct=size_pct,
        position_size_dollars=size_dollars,
        target_date=target_date,
        take_profit_price=tp_price,
        stop_loss_price=sl_price,
        trailing_tiers=config.trailing_stop_tiers,
        confidence=confidence,
        ev=ev,
        prediction=prediction,
        had_survivorship_warning=had_survivorship_warning,
    )


def compute_target_date(entry_date: date, holding_period_max_days: int, ticker_trading_dates: list[date]) -> date:
    """El Nº-ésimo día de negociación posterior a entry_date, usando el
    calendario REAL del propio ticker (no días de calendario — un holding de
    "5 días" cruzando un fin de semana no debe encogerse). Si el ticker no
    tiene suficientes días futuros en los datos (fin del panel de precios),
    se usa el último día disponible — el caller debe forzar el cierre ahí."""
    future = sorted(d for d in ticker_trading_dates if d > entry_date)
    if len(future) >= holding_period_max_days:
        return future[holding_period_max_days - 1]
    return future[-1] if future else entry_date


# ============================================================================
# Orquestación contra Postgres — fetch de eventos, bucle día a día, escritura.
# ============================================================================

VERSIONS = ("CONSERVATIVE", "AGGRESSIVE", "BALANCED")


def fetch_events_for_version(conn, version: str) -> list[dict]:
    """Eventos con trade_decision != NO_TRADE para `version`. Trae SIEMPRE
    los 3 ev_* (no solo el de la versión) porque BALANCED necesita
    ev_conservative Y ev_aggressive para decidir el estilo de ejecución
    (classify_balanced_execution_style) — pedirlos todos es más simple que
    dos queries distintas según la versión."""
    assert version in VERSIONS, f"versión desconocida: {version}"
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
            ORDER BY e.d0_close_date
            """
        )
        return cur.fetchall()


def _load_ticker_prices(conn, ticker: str) -> dict[date, dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date, open_raw, high_raw, low_raw, close_raw, survivorship_warning "
            "FROM prices WHERE ticker = %s ORDER BY trade_date",
            (ticker,),
        )
        return {r["trade_date"]: r for r in cur.fetchall()}


def _build_entry_plan(conn, version: str, events: list[dict], ticker_cache: dict[str, dict[date, dict]]) -> list[dict]:
    """Precalcula, por evento, la fecha de entrada real (primer día de
    negociación del TICKER estrictamente posterior a d0_close_date — no el
    calendario maestro, por si ese ticker en concreto tiene un hueco justo
    ahí), el estilo de ejecución, y target_date. Eventos sin datos de precio
    suficientes se omiten con un warning, no con un crash."""
    plan = []
    for ev in events:
        ticker = ev["ticker"]
        if ticker not in ticker_cache:
            ticker_cache[ticker] = _load_ticker_prices(conn, ticker)
        prices = ticker_cache[ticker]

        future_dates = sorted(d for d in prices if d > ev["d0_close_date"])
        if not future_dates:
            logger.warning("Evento %d (%s): sin precios posteriores a D0, se omite", ev["event_id"], ticker)
            continue
        entry_date = future_dates[0]
        entry_bar = prices[entry_date]
        if entry_bar["open_raw"] is None:
            logger.warning("Evento %d (%s): sin precio de apertura en la fecha de entrada, se omite", ev["event_id"], ticker)
            continue

        direction = "LONG" if float(ev["prediction"]) > 0 else "SHORT"
        if version == "BALANCED":
            style = classify_balanced_execution_style(float(ev["confidence"]), float(ev["ev_conservative"]), float(ev["ev_aggressive"]))
            ev_value = float(ev["ev_conservative"]) if style == "CONSERVATIVE" else float(ev["ev_aggressive"])
        else:
            style = version
            ev_value = float(ev["ev_conservative"] if version == "CONSERVATIVE" else ev["ev_aggressive"])

        config = STRATEGIES[style]
        target_date = compute_target_date(entry_date, config.holding_period_max_days, sorted(prices.keys()))

        plan.append(
            {
                "event_id": ev["event_id"],
                "ticker": ticker,
                "entry_date": entry_date,
                "execution_style": style,
                "direction": direction,
                "ev": ev_value,
                "confidence": float(ev["confidence"]),
                "prediction": float(ev["prediction"]),
                "target_date": target_date,
            }
        )
    return plan


def simulate_portfolio(conn, version: str, run_batch_tag: str, starting_capital: float = 100_000.0) -> dict:
    """Punto de entrada del backtest de cartera para UNA versión de
    estrategia. Ver docstring del módulo para la disciplina anti-look-ahead
    y las decisiones de diseño (orden de prioridad TP/SL/trailing, MTM de
    posiciones parcialmente cerradas, etc.)."""
    assert version in VERSIONS

    events = fetch_events_for_version(conn, version)
    if not events:
        logger.warning("Sin eventos con trade_decision para %s — nada que simular", version)
        return {"version": version, "n_trades": 0, "n_equity_days": 0}

    ticker_cache: dict[str, dict[date, dict]] = {}
    entry_plan = _build_entry_plan(conn, version, events, ticker_cache)
    if not entry_plan:
        logger.warning("Ningún evento de %s tiene un plan de entrada válido", version)
        return {"version": version, "n_trades": 0, "n_equity_days": 0}

    entries_by_date: dict[date, list[dict]] = {}
    for p in entry_plan:
        entries_by_date.setdefault(p["entry_date"], []).append(p)

    all_dates: set[date] = set()
    for prices in ticker_cache.values():
        all_dates.update(prices.keys())
    first_entry = min(p["entry_date"] for p in entry_plan)
    master_calendar = sorted(d for d in all_dates if d >= first_entry)

    if version == "BALANCED":
        max_concurrent = dict(BALANCED_MAX_CONCURRENT)
    elif version == "CONSERVATIVE":
        max_concurrent = {"CONSERVATIVE": STRATEGIES["CONSERVATIVE"].max_concurrent, "AGGRESSIVE": 0}
    else:
        max_concurrent = {"CONSERVATIVE": 0, "AGGRESSIVE": STRATEGIES["AGGRESSIVE"].max_concurrent}

    cash = starting_capital
    open_positions: dict[str, list[OpenPosition]] = {"CONSERVATIVE": [], "AGGRESSIVE": []}
    completed_trades: list[dict] = []
    equity_rows: list[dict] = []

    def portfolio_mtm(today: date) -> float:
        total = 0.0
        for style_positions in open_positions.values():
            for pos in style_positions:
                bar = ticker_cache[pos.ticker].get(today)
                price = float(bar["close_raw"]) if bar and bar["close_raw"] is not None else pos.entry_price
                total += compute_position_mtm_dollars(pos, price)
        return total

    for today in master_calendar:
        # 1) Salidas — se procesan antes que las entradas del mismo día.
        for style in ("CONSERVATIVE", "AGGRESSIVE"):
            still_open = []
            for pos in open_positions[style]:
                bar = ticker_cache[pos.ticker].get(today)
                if bar is None or bar["high_raw"] is None or bar["low_raw"] is None or bar["close_raw"] is None:
                    still_open.append(pos)  # sin dato ese día (festivo local/halt) — se mantiene abierta
                    continue
                step_position_forward(pos, float(bar["high_raw"]), float(bar["low_raw"]), float(bar["close_raw"]), today)
                if pos.remaining_fraction <= 1e-9:
                    record = consolidate_trade_record(pos)
                    record["version"] = version
                    record["run_batch_tag"] = run_batch_tag
                    completed_trades.append(record)
                    cash += pos.position_size_dollars + record["pnl_abs"]
                else:
                    still_open.append(pos)
            open_positions[style] = still_open

        # 2) Entradas — dimensionadas contra la equity de HOY tras las salidas
        # de hoy (spec: "rebalance: noche antes de apertura").
        equity_for_sizing = cash + portfolio_mtm(today)
        for plan in entries_by_date.get(today, []):
            style = plan["execution_style"]
            if len(open_positions[style]) >= max_concurrent[style]:
                continue  # sin hueco — la señal se descarta (max_concurrent del spec)
            bar = ticker_cache[plan["ticker"]].get(today)
            if bar is None or bar["open_raw"] is None:
                continue
            pos = open_position(
                event_id=plan["event_id"], version=version, execution_style=style, direction=plan["direction"],
                ticker=plan["ticker"], entry_date=today, entry_price=float(bar["open_raw"]), target_date=plan["target_date"],
                balance_for_sizing=equity_for_sizing, confidence=plan["confidence"], ev=plan["ev"], prediction=plan["prediction"],
                had_survivorship_warning=bool(bar["survivorship_warning"]),
            )
            if pos.position_size_dollars > cash:
                logger.warning("Evento %d: tamaño deseado %.2f excede el cash disponible %.2f — se reduce", plan["event_id"], pos.position_size_dollars, cash)
                pos.position_size_dollars = max(cash, 0.0)
            cash -= pos.position_size_dollars
            open_positions[style].append(pos)

        # 3) Curva de equity de hoy (tras salidas Y entradas de hoy).
        n_open = sum(len(v) for v in open_positions.values())
        equity_rows.append({"trade_date": today, "balance": cash + portfolio_mtm(today), "n_open_positions": n_open})

    # Cierre forzado de lo que siga abierto al final de los datos disponibles.
    for style_positions in open_positions.values():
        for pos in style_positions:
            last_date = max(ticker_cache[pos.ticker].keys())
            last_close = ticker_cache[pos.ticker][last_date]["close_raw"]
            pos.closes.append((last_date, pos.remaining_fraction, float(last_close), "MAX_HOLDING"))
            pos.remaining_fraction = 0.0
            record = consolidate_trade_record(pos)
            record["version"] = version
            record["run_batch_tag"] = run_batch_tag
            completed_trades.append(record)

    _store_portfolio_results(conn, version, run_batch_tag, completed_trades, equity_rows)
    return {"version": version, "n_trades": len(completed_trades), "n_equity_days": len(equity_rows)}


def _store_portfolio_results(conn, version: str, run_batch_tag: str, trades: list[dict], equity_rows: list[dict]) -> None:
    with conn.cursor() as cur:
        for t in trades:
            cur.execute(
                """
                INSERT INTO portfolio_trades (
                    event_id, version, execution_style, direction, entry_date, entry_price,
                    exit_date, exit_price, exit_reason, pnl_pct, pnl_abs, position_size_pct,
                    position_size_dollars, confidence, ev, prediction, actual_move_pct,
                    had_survivorship_warning, run_batch_tag
                ) VALUES (
                    %(event_id)s, %(version)s, %(execution_style)s, %(direction)s, %(entry_date)s, %(entry_price)s,
                    %(exit_date)s, %(exit_price)s, %(exit_reason)s, %(pnl_pct)s, %(pnl_abs)s, %(position_size_pct)s,
                    %(position_size_dollars)s, %(confidence)s, %(ev)s, %(prediction)s, %(actual_move_pct)s,
                    %(had_survivorship_warning)s, %(run_batch_tag)s
                )
                ON CONFLICT (event_id, version, run_batch_tag) DO UPDATE SET
                    execution_style = EXCLUDED.execution_style, direction = EXCLUDED.direction,
                    entry_date = EXCLUDED.entry_date, entry_price = EXCLUDED.entry_price,
                    exit_date = EXCLUDED.exit_date, exit_price = EXCLUDED.exit_price,
                    exit_reason = EXCLUDED.exit_reason, pnl_pct = EXCLUDED.pnl_pct, pnl_abs = EXCLUDED.pnl_abs,
                    position_size_pct = EXCLUDED.position_size_pct, position_size_dollars = EXCLUDED.position_size_dollars,
                    confidence = EXCLUDED.confidence, ev = EXCLUDED.ev, prediction = EXCLUDED.prediction,
                    actual_move_pct = EXCLUDED.actual_move_pct, had_survivorship_warning = EXCLUDED.had_survivorship_warning
                """,
                t,
            )
        for row in equity_rows:
            cur.execute(
                """
                INSERT INTO portfolio_equity_curve (version, trade_date, balance, n_open_positions, run_batch_tag)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (version, trade_date, run_batch_tag) DO UPDATE SET
                    balance = EXCLUDED.balance, n_open_positions = EXCLUDED.n_open_positions
                """,
                (version, row["trade_date"], row["balance"], row["n_open_positions"], run_batch_tag),
            )
    conn.commit()
