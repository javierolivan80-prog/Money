"""adversarial_analyzer.py — motor Bull/Bear/Judge sobre cada evento.

DISEÑO (pedido por el spec, "2 LLMs adversariales (Bull/Bear) + Judge, EV engine"):
  1. Por cada evento: un request Bull (busca la tesis alcista más fuerte) y un
     request Bear (tesis bajista más fuerte), EN PARALELO, en el mismo batch.
  2. Un segundo batch de Judge: recibe ambas tesis y arbitra un veredicto final
     con expected_move_pct y confidence.
  3. ev_score = judge_expected_move_pct * judge_confidence — el número que
     entra en backtest/backtester.py:decide_trade().

Por qué dos LLMs adversariales y no uno solo: un único modelo pidiendo "dame
tu mejor estimación" tiende a anclarse en la dirección más obvia del evento
(un 8-K de bankruptcy siempre "parece" bajista). Forzar un Bull y un Bear
inventados obliga a explorar el caso contrario aunque sea débil, y es el Judge
quien decide si ese caso contrario tenía mérito. Esto es una heurística de
diseño, no algo validado empíricamente — el propio backtest (T8, calibración)
es lo que dirá si aporta algo sobre pedir una sola estimación directa.

COSTE (ARCHITECTURE_LEAN.md §6): Haiku 4.5 + Batch API (-50%). Con caché de
prompt sobre el system prompt compartido (misma taxonomía en las ~25k
llamadas), el coste de backfill cae más. Ver la sección de coste en ese
documento para las cifras exactas.

ADVERTENCIA DE VALIDACIÓN: no hay ANTHROPIC_API_KEY disponible en este
sandbox, así que estas llamadas NO se han ejecutado contra la API real en
esta sesión. El formato de request/response sigue la documentación vigente
del SDK (Batch API + output_config.format con json_schema — ver
pipeline/tests/test_adversarial_analyzer.py para las partes que SÍ se
prueban sin red: el cálculo de ev_score y el parseo de una respuesta con la
forma exacta que la API devuelve). Antes del backfill completo, correr
--smoke-test contra 3-5 eventos reales y revisar las tesis a mano.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from pipeline import config

logger = logging.getLogger(__name__)

BULL_BEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string", "description": "Argumento en 2-4 frases, específico al evento"},
        "expected_move_pct": {
            "type": "number",
            "description": "Movimiento de precio esperado en % en la ventana D+1 a D+20, con signo",
        },
        "confidence": {"type": "number", "description": "0.0 a 1.0"},
    },
    "required": ["thesis", "expected_move_pct", "confidence"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "description": "Resumen del arbitraje en 2-4 frases"},
        "expected_move_pct": {"type": "number"},
        "confidence": {"type": "number", "description": "0.0 a 1.0"},
    },
    "required": ["verdict", "expected_move_pct", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT_ANALYST = """\
Eres un analista de eventos corporativos. Analizas un evento normalizado
(8-K de EDGAR o acción de la FDA) y debes argumentar UNA tesis direccional
sobre el precio de la acción en la ventana D+1 a D+20 tras el evento.

Reglas estrictas:
- Usa SOLO la información del evento proporcionado. Nunca asumas datos de
  precio, noticias o resultados posteriores a la fecha del evento — esto
  invalidaría el backtest por look-ahead.
- expected_move_pct es tu estimación puntual del retorno acumulado en la
  ventana, con signo (negativo = bajista).
- confidence refleja qué tan fuerte es tu propia tesis, no la probabilidad
  de que el mercado esté de acuerdo contigo.
- Sé específico al evento. Evita genéricos ("el mercado reaccionará según
  las condiciones") — eso es un thesis vacío y no aporta al Judge.
"""

SYSTEM_PROMPT_JUDGE = """\
Eres un juez que arbitra entre dos analistas (Bull y Bear) que han dado
tesis opuestas sobre el mismo evento corporativo. Tu trabajo es sintetizar
un veredicto final: qué argumento pesa más, y por qué, y una estimación
final de expected_move_pct y confidence que refleje ese arbitraje — no un
promedio mecánico de las dos posturas.
"""


@dataclass
class EventContext:
    event_id: int
    ticker: str
    event_class: str
    company_name: str
    filing_excerpt: str  # texto del filing, recortado — NUNCA incluye precios posteriores a D0


def _event_prompt(ctx: EventContext) -> str:
    return (
        f"Evento: {ctx.event_class}\n"
        f"Empresa: {ctx.company_name} ({ctx.ticker})\n"
        f"Extracto del filing:\n{ctx.filing_excerpt}\n"
    )


def build_bull_bear_batch(events: list[EventContext]):
    """Construye las requests de Batch API para las tesis Bull y Bear.

    Devuelve un objeto Request por (evento, lado) — 2N requests por N eventos.
    custom_id codifica event_id y lado ('bull'/'bear') para poder recomponer
    los resultados sin depender del orden (la Batch API los devuelve en
    cualquier orden — ver batches.md del skill claude-api).
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests_ = []
    for ctx in events:
        for side, extra_instruction in [
            ("bull", "Argumenta el caso MÁS ALCISTA posible para este evento."),
            ("bear", "Argumenta el caso MÁS BAJISTA posible para este evento."),
        ]:
            requests_.append(
                Request(
                    custom_id=f"{ctx.event_id}:{side}",
                    params=MessageCreateParamsNonStreaming(
                        model=config.ANALYZER_MODEL,
                        max_tokens=1024,
                        system=[
                            {
                                "type": "text",
                                "text": SYSTEM_PROMPT_ANALYST,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                        messages=[{"role": "user", "content": f"{extra_instruction}\n\n{_event_prompt(ctx)}"}],
                        output_config={"format": {"type": "json_schema", "schema": BULL_BEAR_SCHEMA}},
                    ),
                )
            )
    return requests_


def build_judge_batch(events: list[EventContext], bull_bear_results: dict[str, dict]):
    """bull_bear_results: {"<event_id>:bull": {...parsed json...}, "<event_id>:bear": {...}}"""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests_ = []
    for ctx in events:
        bull = bull_bear_results.get(f"{ctx.event_id}:bull")
        bear = bull_bear_results.get(f"{ctx.event_id}:bear")
        if bull is None or bear is None:
            logger.warning("Evento %d sin Bull o Bear completo, se omite del Judge", ctx.event_id)
            continue
        prompt = (
            f"{_event_prompt(ctx)}\n"
            f"TESIS BULL (expected_move={bull['expected_move_pct']}%, confidence={bull['confidence']}):\n"
            f"{bull['thesis']}\n\n"
            f"TESIS BEAR (expected_move={bear['expected_move_pct']}%, confidence={bear['confidence']}):\n"
            f"{bear['thesis']}\n"
        )
        requests_.append(
            Request(
                custom_id=f"{ctx.event_id}:judge",
                params=MessageCreateParamsNonStreaming(
                    model=config.ANALYZER_MODEL,
                    max_tokens=1024,
                    system=[{"type": "text", "text": SYSTEM_PROMPT_JUDGE, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
                ),
            )
        )
    return requests_


def run_batch_and_collect(client, requests_) -> dict[str, dict]:
    """Envía un batch, espera a que termine, y devuelve {custom_id: parsed_json}.
    Errores de validación o servidor se registran y se omiten (no abortan el
    batch entero) — un evento sin análisis simplemente no entra al backtest,
    lo cual es preferible a que un solo fallo tumbe miles de resultados buenos.
    """
    batch = client.messages.batches.create(requests=requests_)
    logger.info("Batch creado: %s (%d requests)", batch.id, len(requests_))

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        logger.info("Batch %s: %s", batch.id, batch.processing_status)
        time.sleep(30)

    results: dict[str, dict] = {}
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            logger.warning("Request %s: %s", result.custom_id, result.result.type)
            continue
        text = next((b.text for b in result.result.message.content if b.type == "text"), None)
        if text is None:
            logger.warning("Request %s sin bloque de texto en la respuesta", result.custom_id)
            continue
        try:
            results[result.custom_id] = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Request %s: JSON inválido pese a output_config.format: %r", result.custom_id, text[:200])
    return results, batch.id


def compute_ev_score(expected_move_pct: float, confidence: float) -> float:
    """EV score usado por backtest/backtester.py:decide_trade(). Simple a
    propósito (ARCHITECTURE_LEAN.md: "sin condicionamiento multivariante") —
    una fórmula más compleja con n de cientos sería sobreajuste seguro."""
    return expected_move_pct * confidence


def analyze_events(client, conn, events: list[EventContext]) -> None:
    """Orquesta el flujo completo: Bull+Bear batch -> Judge batch -> guarda en
    la tabla `analyses`. Dos pasadas de batch porque el Judge depende de los
    resultados de Bull/Bear (no se pueden enviar en el mismo batch)."""
    bull_bear_requests = build_bull_bear_batch(events)
    bull_bear_results, bb_batch_id = run_batch_and_collect(client, bull_bear_requests)

    judge_requests = build_judge_batch(events, bull_bear_results)
    judge_results, judge_batch_id = run_batch_and_collect(client, judge_requests)

    with conn.cursor() as cur:
        for ctx in events:
            bull = bull_bear_results.get(f"{ctx.event_id}:bull")
            bear = bull_bear_results.get(f"{ctx.event_id}:bear")
            judge = judge_results.get(f"{ctx.event_id}:judge")
            if not (bull and bear and judge):
                continue
            ev_score = compute_ev_score(judge["expected_move_pct"], judge["confidence"])
            cur.execute(
                """
                INSERT INTO analyses (
                    event_id, model, batch_id, bull_thesis, bull_expected_move_pct, bull_confidence,
                    bear_thesis, bear_expected_move_pct, bear_confidence,
                    judge_verdict, judge_expected_move_pct, judge_confidence, ev_score
                ) VALUES (
                    %(event_id)s, %(model)s, %(batch_id)s, %(bull_thesis)s, %(bull_move)s, %(bull_conf)s,
                    %(bear_thesis)s, %(bear_move)s, %(bear_conf)s,
                    %(verdict)s, %(judge_move)s, %(judge_conf)s, %(ev_score)s
                )
                ON CONFLICT (event_id, model) DO UPDATE SET
                    batch_id = EXCLUDED.batch_id, judge_verdict = EXCLUDED.judge_verdict,
                    judge_expected_move_pct = EXCLUDED.judge_expected_move_pct,
                    judge_confidence = EXCLUDED.judge_confidence, ev_score = EXCLUDED.ev_score
                """,
                {
                    "event_id": ctx.event_id,
                    "model": config.ANALYZER_MODEL,
                    "batch_id": f"{bb_batch_id},{judge_batch_id}",
                    "bull_thesis": bull["thesis"],
                    "bull_move": bull["expected_move_pct"],
                    "bull_conf": bull["confidence"],
                    "bear_thesis": bear["thesis"],
                    "bear_move": bear["expected_move_pct"],
                    "bear_conf": bear["confidence"],
                    "verdict": judge["verdict"],
                    "judge_move": judge["expected_move_pct"],
                    "judge_conf": judge["confidence"],
                    "ev_score": ev_score,
                },
            )
    conn.commit()


if __name__ == "__main__":
    import argparse

    import anthropic

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=int, default=5, help="Nº de eventos para probar antes del backfill completo")
    args = parser.parse_args()

    from pipeline.db.connection import get_connection

    conn = get_connection()
    client = anthropic.Anthropic()  # requiere ANTHROPIC_API_KEY en el entorno

    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.event_id, e.ticker, e.event_class, u.company_name FROM events e "
            "JOIN universe u ON u.cik = e.cik "
            "LEFT JOIN analyses a ON a.event_id = e.event_id "
            "WHERE a.event_id IS NULL LIMIT %s",
            (args.smoke_test,),
        )
        rows = cur.fetchall()

    events = [
        EventContext(
            event_id=r["event_id"],
            ticker=r["ticker"],
            event_class=r["event_class"],
            company_name=r["company_name"],
            filing_excerpt="(placeholder — el texto real del filing se ingiere en edgar_scraper.py)",
        )
        for r in rows
    ]
    if not events:
        print("No hay eventos pendientes de análisis")
    else:
        analyze_events(client, conn, events)
        print(f"Analizados {len(events)} eventos")
