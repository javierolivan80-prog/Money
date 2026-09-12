"""historical_analogues.py — Etapa 6, insumo de Impact Estimation.

Implementa el enfoque que recomienda AUDIT_LEAN.md §2.2.4 para cuando n es
pequeño (cientos, no miles): agrupar por event_class (UNA sola variable de
condicionamiento — condicionar por más, con estos tamaños de muestra, produce
celdas de n=5 y estimaciones que son ruido disfrazado de señal), y aplicar
shrinkage bayesiano hacia la media del grupo cuando el número de análogos es
bajo, en vez de reportar la media cruda de 5-10 observaciones como si fuera
sólida.

Disciplina anti-look-ahead aplicada aquí también: al estimar el impacto de un
evento en la fecha `as_of_date`, los análogos se restringen a eventos con
d0_close_date ANTERIOR — nunca se usa un análogo que "todavía no había
pasado" en el momento de la decisión simulada. Es la misma razón por la que
el resto del proyecto es obsesivo con D0_close_date (ARCHITECTURE_LEAN.md §4):
una distribución de análogos que incluye el futuro es un look-ahead sutil,
fácil de pasar por alto porque no toca precios directamente.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

# Umbral de shrinkage: por debajo de este nº de análogos, la estimación se
# empuja hacia el prior (media de TODOS los análogos de la clase, sin
# restricción temporal, como aproximación de la media poblacional de largo
# plazo) con un peso creciente cuanto menor sea n. n/(n+K) es el estimador de
# James-Stein/empírico-Bayes más simple que existe — deliberadamente simple,
# no un modelo jerárquico completo (mismo principio de parsimonia que en
# ev_engine.py).
SHRINKAGE_K = 15.0
MIN_ANALOGUES_FOR_ANY_CONFIDENCE = 5  # por debajo de esto, confidence se fuerza a un mínimo simbólico


@dataclass
class ImpactEstimate:
    probability_5pct_move: float
    probability_10pct_move: float
    probability_20pct_move: float
    expected_direction: float  # 1.0 / -1.0 / 0.0
    expected_magnitude_pct: float  # con shrinkage ya aplicado
    volatility_increase: float  # 0-100
    confidence: float  # 0-100
    n_analogues: int

    def as_json(self) -> dict:
        return {
            "probability_5pct_move": round(self.probability_5pct_move, 1),
            "probability_10pct_move": round(self.probability_10pct_move, 1),
            "probability_20pct_move": round(self.probability_20pct_move, 1),
            "expected_direction": self.expected_direction,
            "expected_magnitude": f"±{abs(self.expected_magnitude_pct):.2f}% según histórico de {self.n_analogues} eventos análogos",
            "volatility_increase": round(self.volatility_increase, 1),
            "confidence": round(self.confidence, 1),
        }


def compute_impact_estimate(
    analogue_cars_pct: list[float],
    analogue_volume_ratios: list[float],
    class_prior_mean_car_pct: float = 0.0,
) -> ImpactEstimate:
    """Pura, sin I/O — toma listas ya recuperadas de la BD.

    analogue_cars_pct: CAR en % (no fracción) de cada análogo, YA restringido
        por el caller a eventos anteriores a as_of_date de la misma clase.
    class_prior_mean_car_pct: media de la clase SIN restricción temporal — el
        prior hacia el que se contrae la estimación cuando n es bajo. Pasar 0.0
        si no se tiene (equivale a contraer hacia "sin efecto", la opción más
        conservadora posible).
    """
    n = len(analogue_cars_pct)
    if n == 0:
        logger.warning("Cero análogos disponibles — se devuelve una estimación totalmente sin confianza")
        return ImpactEstimate(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    mean_car = sum(analogue_cars_pct) / n
    shrinkage_weight = n / (n + SHRINKAGE_K)  # -> 0 cuando n es chico, -> 1 cuando n es grande
    shrunk_magnitude = shrinkage_weight * mean_car + (1 - shrinkage_weight) * class_prior_mean_car_pct

    prob_5 = sum(1 for c in analogue_cars_pct if abs(c) >= 5.0) / n * 100
    prob_10 = sum(1 for c in analogue_cars_pct if abs(c) >= 10.0) / n * 100
    prob_20 = sum(1 for c in analogue_cars_pct if abs(c) >= 20.0) / n * 100

    if abs(shrunk_magnitude) < 0.1:  # demasiado cerca de 0 tras shrinkage para afirmar dirección
        expected_direction = 0.0
    else:
        expected_direction = 1.0 if shrunk_magnitude > 0 else -1.0

    mean_volume_ratio = sum(analogue_volume_ratios) / len(analogue_volume_ratios) if analogue_volume_ratios else 1.0
    # Mapeo monótono simple de "cuánto más volumen que lo normal" a una escala
    # 0-100. Un ratio de 1.0 (volumen normal) -> 0. Un ratio de 3.0+ -> se
    # satura en 100. Es un proxy de expansión de volatilidad/IV, no IV real
    # (AUDIT_LEAN.md §2.1: sin datos de opciones no hay IV real gratis).
    volatility_increase = max(0.0, min(100.0, (mean_volume_ratio - 1.0) * 50.0))

    # Confianza: crece con n (más análogos = menos ruido, ver el MDE de
    # AUDIT_LEAN.md §2.2.3) y cae con la dispersión relativa de la muestra.
    # Con n por debajo del mínimo simbólico, se fuerza a un techo bajo — no
    # tiene sentido fingir confianza sobre 2-3 observaciones por muchas que
    # coincidan por azar.
    if n < MIN_ANALOGUES_FOR_ANY_CONFIDENCE:
        confidence = min(15.0, n * 3.0)
    else:
        n_component = min(n / 50.0, 1.0) * 70  # hasta 70 puntos solo por tamaño de muestra
        if n > 1:
            variance = sum((c - mean_car) ** 2 for c in analogue_cars_pct) / (n - 1)
            std = variance**0.5
        else:
            std = 0.0
        dispersion_penalty = min(std / 10.0, 1.0) * 30  # hasta -30 puntos por dispersión alta
        confidence = max(0.0, min(100.0, n_component + 30 - dispersion_penalty))

    return ImpactEstimate(
        probability_5pct_move=prob_5,
        probability_10pct_move=prob_10,
        probability_20pct_move=prob_20,
        expected_direction=expected_direction,
        expected_magnitude_pct=shrunk_magnitude,
        volatility_increase=volatility_increase,
        confidence=confidence,
        n_analogues=n,
    )


def get_historical_analogues(conn, event_class: str, as_of_date: date, exclude_event_id: int, window_days: int) -> list[dict]:
    """Recupera CAR de eventos de la misma clase, ANTERIORES a as_of_date
    (anti-look-ahead — ver docstring del módulo), excluyendo el propio evento.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cr.car, cr.abnormal_volume_ratio
            FROM car_results cr
            JOIN events e ON e.event_id = cr.event_id
            WHERE e.event_class = %(event_class)s
              AND e.d0_close_date < %(as_of_date)s
              AND cr.event_id != %(exclude_event_id)s
              AND cr.window_days = %(window_days)s
            """,
            {
                "event_class": event_class,
                "as_of_date": as_of_date,
                "exclude_event_id": exclude_event_id,
                "window_days": window_days,
            },
        )
        return cur.fetchall()


def get_class_prior_mean(conn, event_class: str, window_days: int) -> float:
    """Media de CAR (%) de TODA la clase, sin restricción temporal — el prior
    hacia el que se contrae la estimación cuando hay pocos análogos previos
    a una fecha dada (ver SHRINKAGE_K)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT avg(car) * 100 AS mean_car_pct
            FROM car_results cr
            JOIN events e ON e.event_id = cr.event_id
            WHERE e.event_class = %(event_class)s AND cr.window_days = %(window_days)s
            """,
            {"event_class": event_class, "window_days": window_days},
        )
        row = cur.fetchone()
        mean = row["mean_car_pct"] if row else None
        return float(mean) if mean is not None else 0.0


def estimate_impact_for_event(conn, event_class: str, as_of_date: date, exclude_event_id: int, window_days: int = 20) -> ImpactEstimate:
    analogues = get_historical_analogues(conn, event_class, as_of_date, exclude_event_id, window_days)
    prior = get_class_prior_mean(conn, event_class, window_days)
    cars_pct = [float(a["car"]) * 100 for a in analogues]
    volume_ratios = [float(a["abnormal_volume_ratio"]) for a in analogues if a["abnormal_volume_ratio"] is not None]
    return compute_impact_estimate(cars_pct, volume_ratios, class_prior_mean_car_pct=prior)
