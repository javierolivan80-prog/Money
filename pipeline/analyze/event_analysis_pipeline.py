"""event_analysis_pipeline.py — orquestador de la Fase 2, Etapas 1-8.

Conecta los módulos ya escritos (cada uno probado por separado con datos
sintéticos y, donde aplica, contra Postgres real):
  Etapa 1  enrichment.py
  Etapa 2  novelty.py
  Etapas 3-5  adversarial_analyzer.py (Bull/Bear/Judge, con caché de 24h)
  Etapa 6  historical_analogues.py
  Etapa 7  ev_engine.py
  Etapa 8  abstention_engine.py

Precisión importante sobre la caché de 24h (releer el spec: "Cachea
Bull/Bear/Judge"): la caché cubre SOLO las Etapas 3-5 (el veredicto
cualitativo del LLM), NUNCA las Etapas 1/2/6/7/8. Esas cuatro dependen de la
fecha y el precio exactos de CADA evento — reutilizar el EV o la decisión de
abstención de un evento distinto (aunque sea el mismo ticker y la misma
clase) sería aplicar el resultado de "hace 3 horas, con otro precio, con
otros análogos disponibles hasta esa fecha" a un evento que tiene su propio
D0 distinto. Eso rompería la disciplina anti-look-ahead del resto del
proyecto sin que ninguna prueba lo detectara fácilmente. Por eso
process_chunk() extrae del cache_hit SOLO bull_analyst_output/
bear_analyst_output/judge_output/net_conviction/confidence_in_conviction, y
recalcula todo lo demás para el evento actual.

PROCESAMIENTO EN LOTES DE 50 (pedido por el spec): no es un límite de la
Batch API (que admite hasta 100k requests por batch) — es un tamaño de chunk
de orquestación para poder loguear progreso y no perder todo un backfill de
50k eventos si algo falla a mitad de camino.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from pipeline import config
from pipeline.analyze.abstention_engine import AbstentionInputs, as_json as abstention_as_json, decide_all_strategies
from pipeline.analyze.adversarial_analyzer import (
    EventContext,
    build_bull_bear_batch,
    build_judge_batch,
    get_cached_analysis,
    run_batch_and_collect,
)
from pipeline.analyze.enrichment import fetch_and_compute_enrichment
from pipeline.analyze.ev_engine import compute_ev
from pipeline.analyze.guidance_detector import compute_novelty_signals
from pipeline.analyze.historical_analogues import estimate_impact_for_event
from pipeline.analyze.novelty import NoveltyInputs, compute_novelty

_FALLBACK_FILING_EXCERPT = "(sin texto de filing extraído todavía — ver ingest/filing_text.py)"

logger = logging.getLogger(__name__)

CHUNK_SIZE = 50
FDA_CRL_8K_WINDOW_DAYS = 10  # ventana de tolerancia para buscar un 8-K correspondiente


def fetch_events_needing_analysis(conn, limit: int = CHUNK_SIZE) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, e.cik, e.ticker, e.event_class, e.source, e.d0_close_date,
                   e.filing_text, u.company_name, u.sic_code
            FROM events e
            JOIN universe u ON u.cik = e.cik
            LEFT JOIN event_analyses ea ON ea.event_id = e.event_id
            WHERE ea.event_id IS NULL
            ORDER BY e.event_id
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def check_fda_crl_without_8k(conn, cik: str, event_class: str, d0_close_date: date) -> bool:
    """Regla 6 de abstention_engine: una CRL de FDA sin 8-K correspondiente
    todavía no está comunicada oficialmente por la empresa. Solo aplica a
    eventos FDA_CRL — cualquier otra clase devuelve False sin consultar la BD.

    La ventana mira hacia ATRÁS y hasta D0 inclusive, nunca más allá. Antes se
    extendía FDA_CRL_8K_WINDOW_DAYS también hacia delante, lo que respondía a
    una pregunta distinta de la que plantea la regla: "¿acabará la empresa
    comunicando esto?" en vez de "¿lo ha comunicado ya?". Con la ventana
    futura, el sistema dejaba de abstenerse justo en los casos en que un 8-K
    posterior confirmaba el evento — es decir, usaba el futuro para decidir
    operar en el presente. Acotarla a D0 devuelve la regla a su intención y es
    además el lado conservador: ante la duda, abstenerse.
    """
    if event_class != "FDA_CRL":
        return False
    window_start = d0_close_date - timedelta(days=FDA_CRL_8K_WINDOW_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM events
            WHERE cik = %(cik)s AND source = 'EDGAR'
              AND d0_close_date BETWEEN %(start)s AND %(as_of)s
            LIMIT 1
            """,
            {"cik": cik, "start": window_start, "as_of": d0_close_date},
        )
        return cur.fetchone() is None


def process_chunk(conn, client, event_rows: list[dict]) -> None:
    """event_rows: filas de fetch_events_needing_analysis()."""
    cache_hits: dict[int, dict] = {}
    needs_llm: list[dict] = []
    for ev in event_rows:
        cached = get_cached_analysis(conn, ev["ticker"], ev["event_class"], ev["d0_close_date"])
        if cached:
            cache_hits[ev["event_id"]] = cached
        else:
            needs_llm.append(ev)

    logger.info("Chunk de %d eventos: %d en caché, %d requieren LLM", len(event_rows), len(cache_hits), len(needs_llm))

    bull_bear_results: dict[str, dict] = {}
    judge_results: dict[str, dict] = {}
    bb_batch_id = None
    judge_batch_id = None

    if needs_llm:
        contexts = [
            EventContext(
                event_id=ev["event_id"],
                ticker=ev["ticker"],
                event_class=ev["event_class"],
                company_name=ev["company_name"],
                # Fase 3: texto real del filing cuando existe (ingest/filing_text.py
                # ya lo extrajo); si no, degrada al placeholder — un evento sin
                # texto todavía no debe bloquear el análisis, solo empobrecerlo.
                filing_excerpt=ev["filing_text"] or _FALLBACK_FILING_EXCERPT,
            )
            for ev in needs_llm
        ]
        bull_bear_results, bb_batch_id = run_batch_and_collect(client, build_bull_bear_batch(contexts))
        judge_results, judge_batch_id = run_batch_and_collect(client, build_judge_batch(contexts, bull_bear_results))

    for ev in event_rows:
        try:
            _process_single_event(conn, ev, cache_hits.get(ev["event_id"]), bull_bear_results, judge_results, bb_batch_id, judge_batch_id)
        except Exception:
            logger.exception("Fallo analizando evento %d — se continúa con el siguiente", ev["event_id"])
            # Sin rollback, "se continúa con el siguiente" es mentira cuando el
            # fallo viene de Postgres: la transacción queda abortada y TODOS
            # los eventos siguientes fallan con "current transaction is
            # aborted". Pasó exactamente así en la ingesta de fundamentales
            # (run 34943861450): un error real y 134 copias de su consecuencia.
            conn.rollback()


def _process_single_event(conn, ev: dict, cache_hit: dict | None, bull_bear_results: dict, judge_results: dict, bb_batch_id: str | None, judge_batch_id: str | None) -> None:
    event_id = ev["event_id"]

    # --- Etapa 1: enrichment (SIEMPRE fresco — ver docstring del módulo) ---
    enrichment = fetch_and_compute_enrichment(conn, ev)

    # --- Etapa 2: novelty (SIEMPRE fresco) ---
    # Fase 3: has_prior_guidance/rumor_flag ya no son siempre None — se
    # calculan sobre filing_text de eventos previos del mismo ticker (ver
    # guidance_detector.py). Si esos filings aún no tienen texto extraído,
    # compute_novelty_signals devuelve None y compute_novelty renormaliza
    # pesos igual que antes (comportamiento sin cambios en ese caso).
    has_guidance, rumor_flag = compute_novelty_signals(conn, ev["ticker"], ev["d0_close_date"])
    novelty = compute_novelty(
        NoveltyInputs(
            pre_event_drift_pct=enrichment.pre_event_drift_pct or 0.0,
            has_prior_guidance=has_guidance,
            rumor_flag=rumor_flag,
        )
    )

    # --- Etapas 3-5: Bull/Bear/Judge (de caché o de LLM) ---
    from_cache = cache_hit is not None
    if from_cache:
        bull_output = cache_hit["bull_analyst_output"]
        bear_output = cache_hit["bear_analyst_output"]
        judge_output = cache_hit["judge_output"]
        net_conviction = float(cache_hit["net_conviction"])
        confidence_in_conviction = float(cache_hit["confidence_in_conviction"])
        model_bull_bear = cache_hit["model_version_bull_bear"]
        model_judge = cache_hit["model_version_judge"]
        batch_bb, batch_judge = None, None
    else:
        bull_output = bull_bear_results.get(f"{event_id}:bull")
        bear_output = bull_bear_results.get(f"{event_id}:bear")
        judge_output = judge_results.get(f"{event_id}:judge")
        if not (bull_output and bear_output and judge_output):
            logger.warning("Evento %d sin Bull/Bear/Judge completo tras el batch — se omite", event_id)
            return
        net_conviction = float(judge_output["net_conviction"])
        confidence_in_conviction = float(judge_output["confidence_in_conviction"])
        model_bull_bear = config.ANALYZER_MODEL
        model_judge = config.JUDGE_MODEL
        batch_bb, batch_judge = bb_batch_id, judge_batch_id

    # --- Etapa 6: impact estimation (SIEMPRE fresco — depende de as_of_date) ---
    impact = estimate_impact_for_event(conn, ev["event_class"], ev["d0_close_date"], event_id, window_days=20)

    # --- Etapa 7: EV engine ---
    ev_result = compute_ev(net_conviction, confidence_in_conviction, impact.expected_magnitude_pct, impact.confidence)

    # --- Etapa 8: abstention engine ---
    is_fda_crl_without_8k = check_fda_crl_without_8k(conn, ev["cik"], ev["event_class"], ev["d0_close_date"])
    abstention_inputs = AbstentionInputs(
        novelty_score=novelty.score,
        confidence_in_conviction=confidence_in_conviction,
        net_conviction=net_conviction,
        ev_by_strategy={"CONSERVATIVE": ev_result.ev_conservative, "BALANCED": ev_result.ev_balanced, "AGGRESSIVE": ev_result.ev_aggressive},
        had_survivorship_warning=enrichment.had_survivorship_warning,
        beta_available=enrichment.beta_vs_spy is not None,
        high_low_range_pct=enrichment.high_low_range_pct,
        is_fda_crl_without_8k=is_fda_crl_without_8k,
    )
    decisions = decide_all_strategies(abstention_inputs)

    _store_event_analysis(
        conn, event_id, novelty, bull_output, bear_output, judge_output,
        net_conviction, confidence_in_conviction, impact, ev_result, decisions,
        model_bull_bear, model_judge, batch_bb, batch_judge, from_cache,
    )


def _store_event_analysis(conn, event_id, novelty, bull_output, bear_output, judge_output,
                           net_conviction, confidence_in_conviction, impact, ev_result, decisions,
                           model_bull_bear, model_judge, batch_bb, batch_judge, from_cache) -> None:
    import json as _json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge,
                batch_id_bull_bear, batch_id_judge, from_cache
            ) VALUES (
                %(event_id)s, %(novelty_score)s, %(novelty_reasoning)s, %(bull)s, %(bear)s,
                %(judge)s, %(net_conviction)s, %(confidence)s, %(impact)s,
                %(n_analogues)s, %(ev_calc)s, %(ev_cons)s, %(ev_aggr)s, %(ev_bal)s,
                %(abstention)s, %(td_cons)s, %(td_aggr)s, %(td_bal)s, %(model_bb)s, %(model_j)s,
                %(batch_bb)s, %(batch_j)s, %(from_cache)s
            )
            ON CONFLICT (event_id) DO UPDATE SET
                novelty_score = EXCLUDED.novelty_score, novelty_reasoning = EXCLUDED.novelty_reasoning,
                bull_analyst_output = EXCLUDED.bull_analyst_output, bear_analyst_output = EXCLUDED.bear_analyst_output,
                judge_output = EXCLUDED.judge_output, net_conviction = EXCLUDED.net_conviction,
                confidence_in_conviction = EXCLUDED.confidence_in_conviction, impact_estimation = EXCLUDED.impact_estimation,
                n_historical_analogues = EXCLUDED.n_historical_analogues, ev_calculation = EXCLUDED.ev_calculation,
                ev_conservative = EXCLUDED.ev_conservative, ev_aggressive = EXCLUDED.ev_aggressive,
                ev_balanced = EXCLUDED.ev_balanced, abstention_decision = EXCLUDED.abstention_decision,
                trade_decision_conservative = EXCLUDED.trade_decision_conservative,
                trade_decision_aggressive = EXCLUDED.trade_decision_aggressive,
                trade_decision_balanced = EXCLUDED.trade_decision_balanced, analyzed_at = now()
            """,
            {
                "event_id": event_id,
                "novelty_score": novelty.score,
                "novelty_reasoning": _json.dumps(novelty.as_json()),
                "bull": _json.dumps(bull_output),
                "bear": _json.dumps(bear_output),
                "judge": _json.dumps(judge_output),
                "net_conviction": net_conviction,
                "confidence": confidence_in_conviction,
                "impact": _json.dumps(impact.as_json()),
                "n_analogues": impact.n_analogues,
                "ev_calc": _json.dumps(ev_result.as_json()),
                "ev_cons": ev_result.ev_conservative,
                "ev_aggr": ev_result.ev_aggressive,
                "ev_bal": ev_result.ev_balanced,
                "abstention": _json.dumps(abstention_as_json(decisions)),
                "td_cons": decisions["CONSERVATIVE"].trade_decision,
                "td_aggr": decisions["AGGRESSIVE"].trade_decision,
                "td_bal": decisions["BALANCED"].trade_decision,
                "model_bb": model_bull_bear,
                "model_j": model_judge,
                "batch_bb": batch_bb,
                "batch_j": batch_judge,
                "from_cache": from_cache,
            },
        )
    conn.commit()


def run_pipeline(conn, client, max_chunks: int | None = None) -> int:
    """Bucle principal: procesa hasta que no queden eventos pendientes.
    Devuelve el nº total de eventos procesados (con éxito o con error
    individual — un fallo por evento no cuenta como "no procesado" a efectos
    del bucle, ya que _process_single_event ya lo atrapó y logueó)."""
    total = 0
    chunks_done = 0
    while max_chunks is None or chunks_done < max_chunks:
        batch = fetch_events_needing_analysis(conn, CHUNK_SIZE)
        if not batch:
            break
        process_chunk(conn, client, batch)
        total += len(batch)
        chunks_done += 1
    return total


def compute_day3_stats(conn) -> dict:
    """Estadísticas de validación del día 3 (pedidas por el spec): % TRADE
    por versión de estrategia y promedios de novelty/EV/confidence.

    NO auto-ajusta los umbrales aunque el % esté fuera del rango 30-40%
    esperado — solo lo reporta con una recomendación textual. Auto-ajustar
    umbrales contra la salida de la MISMA corrida que se está evaluando es
    exactamente el tipo de sobreajuste que AUDIT_LEAN.md prohíbe (§9, T6:
    los umbrales son una decisión que se toma UNA VEZ, antes de mirar el
    resultado fuera de muestra, no una que se recalibra mirando el propio
    resultado)."""
    stats: dict = {}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM event_analyses")
        total = cur.fetchone()["n"]
        stats["total_analyzed"] = total

        cur.execute("SELECT avg(novelty_score) AS avg_novelty, avg(confidence_in_conviction) AS avg_confidence, avg(ev_balanced) AS avg_ev_balanced FROM event_analyses")
        row = cur.fetchone()
        stats["avg_novelty_score"] = float(row["avg_novelty"]) if row["avg_novelty"] is not None else None
        stats["avg_confidence_in_conviction"] = float(row["avg_confidence"]) if row["avg_confidence"] is not None else None
        stats["avg_ev_balanced"] = float(row["avg_ev_balanced"]) if row["avg_ev_balanced"] is not None else None

        for strategy, column in [("CONSERVATIVE", "trade_decision_conservative"), ("AGGRESSIVE", "trade_decision_aggressive"), ("BALANCED", "trade_decision_balanced")]:
            cur.execute(f"SELECT count(*) AS n FROM event_analyses WHERE {column} != 'NO_TRADE'")
            trade_count = cur.fetchone()["n"]
            pct = (trade_count / total * 100) if total else 0.0
            stats[f"pct_trade_{strategy.lower()}"] = pct
            if total > 0:
                if pct > 70:
                    stats[f"recommendation_{strategy.lower()}"] = f"% TRADE={pct:.1f}% > 70% — demasiado permisivo, considera subir el umbral de {strategy} en ev_engine.EV_THRESHOLDS (no auto-ajustado, ver docstring)"
                elif pct < 10:
                    stats[f"recommendation_{strategy.lower()}"] = f"% TRADE={pct:.1f}% < 10% — demasiado restrictivo, considera bajar el umbral de {strategy} (no auto-ajustado)"
                else:
                    stats[f"recommendation_{strategy.lower()}"] = f"% TRADE={pct:.1f}% dentro del rango esperado (10-70%, objetivo 30-40%)"

    return stats


if __name__ == "__main__":
    import json

    import anthropic

    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    conn = get_connection()
    client = anthropic.Anthropic()  # requiere ANTHROPIC_API_KEY

    processed = run_pipeline(conn, client)
    print(f"Procesados {processed} eventos")

    stats = compute_day3_stats(conn)
    print(json.dumps(stats, indent=2))
