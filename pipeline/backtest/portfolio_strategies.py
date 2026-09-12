"""portfolio_strategies.py — configuración de las 3 versiones de estrategia
para el backtest de cartera (portfolio_simulator.py).

El spec deja varios parámetros con un rango o un patrón abierto en vez de un
número exacto. Se documenta cada decisión aquí, no se oculta en el código:

1. Conservative take_profit "+2% o +3%": se usa +2% como el disparador real
   — es la lectura más literalmente "conservadora" (tomar beneficios antes).
   +3% queda como comentario, fácil de cambiar si se prefiere lo contrario.

2. Aggressive trailing stop "cierra 30% si +20%, 30% más si +40%, etc.": el
   "etc." se interpreta como el mismo patrón repitiéndose cada +20% de
   ganancia, cerrando 30% de la posición ORIGINAL en cada tramo, hasta
   agotar la posición (el último tramo cierra lo que quede, aunque sea menos
   del 30%). Ver generate_trailing_stop_tiers().

3. BALANCED como mezcla de dos estilos de ejecución: el spec da sizing y
   holding period "por trade Conservative" y "por trade Aggressive" DENTRO
   de la misma cartera Balanced, con límites de concurrencia separados
   (3+2=5). Esto solo tiene sentido si cada evento de Balanced se ejecuta
   con las reglas de UNO de los dos estilos, no con una tercera regla nueva.
   classify_balanced_execution_style() decide cuál, con esta lógica
   económica: confianza muy alta (>=70, el propio umbral de Conservative)
   -> estilo Conservative (banca una ganancia modesta rápido cuando la
   convicción es máxima); si no, pero el EV es alto (>=0.5%, el umbral de
   Aggressive) -> estilo Aggressive (deja correr una apuesta más especulativa
   con trailing stop). Si cumple ambos, gana Conservative (proteger capital
   cuando ambos aplican). La decisión de SI se opera en absoluto para
   BALANCED sigue viniendo de event_analyses.trade_decision_balanced (Fase 2,
   ya probado) — esto solo decide CÓMO ejecutar el trade, no si.

4. Tamaño de posición dentro de la banda [min,max]: escala linealmente con
   confidence_in_conviction entre el umbral de la estrategia (-> min) y 100
   (-> max). Ata el tamaño a la convicción del Judge, que es la única señal
   de "qué tan fuerte es esta idea" disponible en la decisión.
"""
from __future__ import annotations

from dataclasses import dataclass

EXECUTION_STYLES = ("CONSERVATIVE", "AGGRESSIVE")


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    position_size_min_pct: float
    position_size_max_pct: float
    holding_period_max_days: int
    take_profit_pct: float | None       # None si usa trailing_stop_tiers en su lugar
    stop_loss_pct: float
    trailing_stop_tiers: tuple[tuple[float, float], ...] | None  # (umbral_ganancia_%, fracción_a_cerrar)
    confidence_threshold: float          # 0-100
    ev_threshold: float                  # fracción, no % (0.002 = 0.2%)
    max_concurrent: int


def generate_trailing_stop_tiers(increment_pct: float = 20.0, close_fraction: float = 0.30) -> tuple[tuple[float, float], ...]:
    """Genera los tramos del trailing stop de Aggressive: cada `increment_pct`
    de ganancia cierra `close_fraction` de la posición ORIGINAL, hasta agotarla.
    El último tramo cierra lo que quede (puede ser menos de close_fraction)."""
    tiers = []
    remaining = 1.0
    threshold = increment_pct
    while remaining > 1e-9:
        fraction = min(close_fraction, remaining)
        tiers.append((threshold, fraction))
        remaining -= fraction
        threshold += increment_pct
    return tuple(tiers)


STRATEGIES: dict[str, StrategyConfig] = {
    "CONSERVATIVE": StrategyConfig(
        name="CONSERVATIVE",
        position_size_min_pct=2.0,
        position_size_max_pct=5.0,
        holding_period_max_days=5,
        take_profit_pct=2.0,  # ver nota 1 del docstring
        stop_loss_pct=1.5,
        trailing_stop_tiers=None,
        confidence_threshold=70.0,
        ev_threshold=0.002,
        max_concurrent=3,
    ),
    "AGGRESSIVE": StrategyConfig(
        name="AGGRESSIVE",
        position_size_min_pct=10.0,
        position_size_max_pct=20.0,
        holding_period_max_days=20,
        take_profit_pct=None,
        stop_loss_pct=5.0,
        trailing_stop_tiers=generate_trailing_stop_tiers(),  # ver nota 2 del docstring
        confidence_threshold=50.0,
        ev_threshold=0.005,
        max_concurrent=2,
    ),
}

# BALANCED no es una tercera configuración independiente — es la combinación
# de las dos anteriores con tamaños de posición reducidos, pedidos
# explícitamente por el spec (1.5% / 5% en vez de las bandas completas de
# arriba). max_concurrent se reparte 3+2 = 5 (ver classify_balanced_execution_style).
BALANCED_POSITION_SIZE_PCT = {
    "CONSERVATIVE": 1.5,
    "AGGRESSIVE": 5.0,
}
BALANCED_MAX_CONCURRENT = {
    "CONSERVATIVE": 3,
    "AGGRESSIVE": 2,
}
# Umbral combinado de Balanced (spec: "trade_threshold: promedio ponderado").
# Pesos 50/50 a propósito — no hay ninguna señal en el spec de que uno de los
# dos estilos deba pesar más que el otro.
BALANCED_CONFIDENCE_THRESHOLD = (STRATEGIES["CONSERVATIVE"].confidence_threshold + STRATEGIES["AGGRESSIVE"].confidence_threshold) / 2
BALANCED_EV_THRESHOLD = (STRATEGIES["CONSERVATIVE"].ev_threshold + STRATEGIES["AGGRESSIVE"].ev_threshold) / 2


def classify_balanced_execution_style(confidence: float, ev_conservative: float, ev_aggressive: float) -> str:
    """Decide con qué reglas (Conservative o Aggressive) se ejecuta un trade
    de la cartera Balanced. Ver nota 3 del docstring del módulo para el
    razonamiento económico. Devuelve 'CONSERVATIVE' o 'AGGRESSIVE' — nunca
    None: se asume que el caller ya sabe (por trade_decision_balanced) que
    este evento SÍ se opera; esta función solo decide el estilo."""
    cons = STRATEGIES["CONSERVATIVE"]
    if confidence >= cons.confidence_threshold and ev_conservative >= cons.ev_threshold:
        return "CONSERVATIVE"
    return "AGGRESSIVE"


def compute_position_size_pct(confidence: float, config: StrategyConfig) -> float:
    """Interpola linealmente entre [min,max] según qué tan por encima está
    confidence del umbral de la propia estrategia. confidence=umbral -> min;
    confidence=100 -> max."""
    span = 100.0 - config.confidence_threshold
    if span <= 0:
        return config.position_size_max_pct
    frac = max(0.0, min(1.0, (confidence - config.confidence_threshold) / span))
    return config.position_size_min_pct + frac * (config.position_size_max_pct - config.position_size_min_pct)


def compute_balanced_position_size_pct(execution_style: str) -> float:
    """Balanced NO interpola por confianza (el spec da un número fijo por
    estilo: 1.5% / 5%) — se respeta literalmente."""
    return BALANCED_POSITION_SIZE_PCT[execution_style]
