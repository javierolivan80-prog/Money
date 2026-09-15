"""adversarial_analyzer.py — Etapas 3-5: Bull, Bear y Judge.

Reescrito en Fase 2 con los esquemas JSON exactos del spec (más ricos que la
Fase 1: drivers/riesgos estructurados, no solo una tesis y un % suelto). La
Fase 1 pedía a Bull/Bear un expected_move_pct numérico; la Fase 2 YA NO — esa
magnitud ahora viene de la Etapa 6 (analyze/historical_analogues.py, basada
en análogos reales, no en la intuición del LLM sobre "cuánto" se moverá el
precio). Bull/Bear en Fase 2 solo aportan CUALIDAD (drivers, riesgos,
comparables); Judge solo aporta DIRECCIÓN Y FUERZA de convicción
(net_conviction), no magnitud. La combinación de dirección (Judge) ×
magnitud (Etapa 6) es exactamente lo que hace analyze/ev_engine.py — ver su
docstring.

Enrutado de modelos, pedido explícito del spec:
  - Bull / Bear: Haiku 4.5 (rápido, barato, tarea de generación de texto
    estructurado, no de arbitraje).
  - Judge: Sonnet 4.6 ("mejor reasoning" — el spec nombra la versión
    explícitamente, así que se usa esa, no la última disponible).

Caché de 24h por (ticker, event_class), pedida por el spec: si ya existe un
event_analyses de la misma combinación en las últimas 24h, se reutiliza en
vez de volver a llamar al LLM — ahorra coste cuando varios eventos del mismo
tipo golpean al mismo ticker en poco tiempo (ej. un 8-K seguido de una
corrección al día siguiente).

ADVERTENCIA DE VALIDACIÓN: sigue sin haber ANTHROPIC_API_KEY en este sandbox
(igual que en la Fase 1) — nada de esto se ha ejecutado contra la API real.
Lo que SÍ se prueba sin red: construcción de las requests de batch, parseo de
resultados con la forma exacta del SDK, y la query de caché contra Postgres
real (ver pipeline/tests/test_adversarial_analyzer.py).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date

from pipeline import config

logger = logging.getLogger(__name__)

BULL_SCHEMA = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string", "description": "Máx 3 frases"},
        "upside_drivers": {"type": "array", "items": {"type": "string"}},
        "addressable_market": {"type": "string", "description": "Tamaño/impacto potencial"},
        "comparable_events": {"type": "string", "description": "¿Este evento es similar a X?"},
        "catalysts_forward": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["thesis", "upside_drivers", "addressable_market", "comparable_events", "catalysts_forward"],
    "additionalProperties": False,
}

BEAR_SCHEMA = {
    "type": "object",
    "properties": {
        "counter_thesis": {"type": "string", "description": "Máx 3 frases"},
        "downside_risks": {"type": "array", "items": {"type": "string"}},
        "valuation_concern": {"type": "string", "description": "¿El stock ya descuenta esto?"},
        "historical_precedent": {"type": "string", "description": "Eventos similares que fracasaron"},
        "negative_catalysts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["counter_thesis", "downside_risks", "valuation_concern", "historical_precedent", "negative_catalysts"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "net_conviction": {"type": "number", "minimum": -1.0, "maximum": 1.0, "description": "-1 = Bear gana, 1 = Bull gana"},
        "confidence_in_conviction": {"type": "number", "minimum": 0, "maximum": 100},
        "key_uncertainty": {"type": "string", "description": "¿Qué dato resolvería el debate?"},
        "overriding_concern": {"type": "string", "description": "Si algo anula a Bull o Bear, cuál es"},
    },
    "required": ["net_conviction", "confidence_in_conviction", "key_uncertainty", "overriding_concern"],
    "additionalProperties": False,
}

SYSTEM_PROMPT_BULL = """\
Eres un analista alcista de eventos corporativos. Construye la mejor tesis
alcista POSIBLE sobre el evento dado — optimista pero no delirante, basada en
hechos del propio evento, nunca en datos posteriores a su fecha (eso
invalidaría el backtest por look-ahead). No inventes cifras que no estén en
el evento o su contexto financiero.
"""

SYSTEM_PROMPT_BEAR = """\
Eres un analista bajista de eventos corporativos. Tu trabajo es DESTRUIR la
tesis alcista más obvia sobre el evento dado: ¿por qué puede fallar? Tono
escéptico y adversarial — no des por buena ninguna narrativa optimista sin
cuestionarla. Basado en hechos del evento, nunca en datos posteriores a su
fecha.
"""

SYSTEM_PROMPT_JUDGE = """\
Eres un juez de riesgo que arbitra entre un analista Bull y un analista Bear
que han argumentado posturas opuestas sobre el mismo evento corporativo. No
promedies mecánicamente las dos posturas: decide cuál pesa más y por qué, y
sé explícito sobre qué dato, de existir, resolvería la incertidumbre central
del debate.
"""

CACHE_WINDOW_HOURS = 24


@dataclass
class EventContext:
    event_id: int
    ticker: str
    event_class: str
    company_name: str
    filing_excerpt: str  # texto del filing, recortado — NUNCA incluye precios posteriores a D0
    financial_context: str = ""  # de event_enrichment (Etapa 1) — resumen legible para el prompt


def _event_prompt(ctx: EventContext) -> str:
    parts = [
        f"Evento: {ctx.event_class}",
        f"Empresa: {ctx.company_name} ({ctx.ticker})",
        f"Extracto del filing:\n{ctx.filing_excerpt}",
    ]
    if ctx.financial_context:
        parts.append(f"Contexto financiero (Etapa 1):\n{ctx.financial_context}")
    return "\n".join(parts)


def custom_id_de(event_id: int, side: str) -> str:
    """El id de cada request dentro de un batch de la Batch API de Anthropic.

    BUG REAL (2026-09-15, run 34960903955): se construía como
    f"{event_id}:{side}", con dos puntos. La API los rechaza:

        requests.0.custom_id: String should match pattern
        '^[a-zA-Z0-9_-]{1,64}$'

    y el batch entero fallaba con 400 antes de procesar una sola request —
    el fallo estaba en la FORMA del identificador, no en su contenido, así
    que no dependía de qué evento fuera. Con guion bajo en vez de dos puntos
    entra dentro del patrón que exige la API.

    Centralizado aquí porque el mismo formato se construye en tres sitios de
    este módulo y se vuelve a parsear en otro más
    (event_analysis_pipeline.py) — repetirlo a mano es la forma en que este
    tipo de discrepancia vuelve a colarse.
    """
    return f"{event_id}_{side}"


def build_bull_bear_batch(events: list[EventContext]):
    """2N requests por N eventos: una Bull, una Bear, cada una con su propio
    esquema JSON y su propio system prompt — ver docstring del módulo."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests_ = []
    for ctx in events:
        for side, system_prompt, schema in [
            ("bull", SYSTEM_PROMPT_BULL, BULL_SCHEMA),
            ("bear", SYSTEM_PROMPT_BEAR, BEAR_SCHEMA),
        ]:
            requests_.append(
                Request(
                    custom_id=custom_id_de(ctx.event_id, side),
                    params=MessageCreateParamsNonStreaming(
                        model=config.ANALYZER_MODEL,
                        max_tokens=1024,
                        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                        messages=[{"role": "user", "content": _event_prompt(ctx)}],
                        output_config={"format": {"type": "json_schema", "schema": schema}},
                    ),
                )
            )
    return requests_


def build_judge_batch(events: list[EventContext], bull_bear_results: dict[str, dict]):
    """bull_bear_results: {"<event_id>:bull": {...}, "<event_id>:bear": {...}}.
    Modelo: config.JUDGE_MODEL (claude-sonnet-4-6, pedido explícito por el spec)."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests_ = []
    for ctx in events:
        bull = bull_bear_results.get(custom_id_de(ctx.event_id, "bull"))
        bear = bull_bear_results.get(custom_id_de(ctx.event_id, "bear"))
        if bull is None or bear is None:
            logger.warning("Evento %d sin Bull o Bear completo, se omite del Judge", ctx.event_id)
            continue
        prompt = (
            f"{_event_prompt(ctx)}\n\n"
            f"TESIS BULL:\n{bull['thesis']}\n"
            f"Drivers alcistas: {', '.join(bull['upside_drivers'])}\n"
            f"Mercado direccionable: {bull['addressable_market']}\n"
            f"Comparables: {bull['comparable_events']}\n\n"
            f"TESIS BEAR:\n{bear['counter_thesis']}\n"
            f"Riesgos bajistas: {', '.join(bear['downside_risks'])}\n"
            f"Preocupación de valoración: {bear['valuation_concern']}\n"
            f"Precedente histórico: {bear['historical_precedent']}\n"
        )
        requests_.append(
            Request(
                custom_id=custom_id_de(ctx.event_id, "judge"),
                params=MessageCreateParamsNonStreaming(
                    model=config.JUDGE_MODEL,
                    max_tokens=1024,
                    system=[{"type": "text", "text": SYSTEM_PROMPT_JUDGE, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
                ),
            )
        )
    return requests_


def run_batch_and_collect(client, requests_) -> tuple[dict[str, dict], str]:
    """Envía un batch, espera a que termine, y devuelve ({custom_id: parsed_json}, batch_id).
    Errores de validación o servidor se registran y se omiten (no abortan el
    batch entero)."""
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


def get_cached_analysis(conn, ticker: str, event_class: str, as_of: date, within_hours: int = CACHE_WINDOW_HOURS) -> dict | None:
    """Busca un event_analyses reciente para (ticker, event_class). Devuelve
    la fila completa (dict) si hay una dentro de la ventana de caché, o None.

    NOTA: la caché se basa en analyzed_at reciente en TÉRMINOS DE RELOJ REAL
    (now() - within_hours), no en la fecha del evento — es una optimización de
    coste de backfill/reruns del pipeline, no una ventana temporal del propio
    evento. Reutilizar un análisis de hace horas para un evento nuevo del
    mismo (ticker, event_class) es una aproximación deliberada: el spec la
    pide explícitamente, aceptando que Bull/Bear/Judge pueden no ser
    idénticos evento a evento dentro de esa ventana.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ea.* FROM event_analyses ea
            JOIN events e ON e.event_id = ea.event_id
            WHERE e.ticker = %(ticker)s AND e.event_class = %(event_class)s
              AND ea.analyzed_at >= now() - (%(hours)s || ' hours')::interval
            ORDER BY ea.analyzed_at DESC
            LIMIT 1
            """,
            {"ticker": ticker, "event_class": event_class, "hours": within_hours},
        )
        return cur.fetchone()


if __name__ == "__main__":
    import argparse

    import anthropic

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=int, default=5, help="Nº de eventos para probar antes del backfill completo")
    args = parser.parse_args()

    from pipeline.db.connection import get_connection

    from pipeline import config

    conn = get_connection()
    # api_key EXPLÍCITO, no el default de la librería (que lee la variable de
    # entorno sin pasar por config.py y por tanto sin el .strip() de un
    # secreto con salto de línea al final — ver la nota en config.py).
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.event_id, e.ticker, e.event_class, u.company_name FROM events e "
            "JOIN universe u ON u.cik = e.cik "
            "LEFT JOIN event_analyses ea ON ea.event_id = e.event_id "
            "WHERE ea.event_id IS NULL LIMIT %s",
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
        bull_bear, bb_id = run_batch_and_collect(client, build_bull_bear_batch(events))
        judge, judge_id = run_batch_and_collect(client, build_judge_batch(events, bull_bear))
        print(f"Bull/Bear: {len(bull_bear)} resultados (batch {bb_id})")
        print(f"Judge: {len(judge)} resultados (batch {judge_id})")
        print("Nota: esto NO escribe en event_analyses — eso lo hace analyze/event_analysis_pipeline.py, "
              "que combina esto con enrichment/novelty/impact/EV/abstention.")
