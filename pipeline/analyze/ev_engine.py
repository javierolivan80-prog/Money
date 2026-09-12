"""ev_engine.py — Etapa 7: motor de valor esperado.

Combina el veredicto del Judge (net_conviction, confidence_in_conviction) con
la Etapa 6 (impact_estimation: magnitud y probabilidades históricas) en un EV
por versión de estrategia, más sizing y el check de umbral simple que pide el
spec. Diseño deliberadamente simple (una fórmula, no un modelo por variable) —
mismo principio que ARCHITECTURE_LEAN.md: con los n de cientos de este POC,
condicionar por más variables es sobreajuste garantizado, no rigor.

Nota de diseño sobre por qué Aggressive tiene MÁS magnitud que Conservative,
no solo un umbral más bajo: el spec da rangos de ejemplo donde Aggressive es
más extremo en ambas direcciones (+2% a -5%) que Conservative (+0.5% a -2%).
Esto solo tiene sentido si Aggressive amplifica la señal cruda (bull/bear) y
Conservative la amortigua — un umbral distinto por sí solo no lo explicaría
(el punto estimado sería el mismo, solo cambiaría el filtro). Por eso
CONSERVATIVE_MULTIPLIER < 1 < AGGRESSIVE_MULTIPLIER: Conservative pide una
señal más fuerte que sobreviva ser amortiguada Y superar un umbral más alto;
Aggressive amplifica la misma señal Y usa un umbral más bajo. Es un efecto
compuesto deliberado, no dos parámetros redundantes.
"""
from __future__ import annotations

from dataclasses import dataclass

# Umbrales de EV por versión de estrategia (fracción, no %): 0.002 = 0.2%.
# Pedidos literalmente por el spec: "¿EV > 0.2%? TRADE / NO TRADE" etc.
EV_THRESHOLDS = {
    "CONSERVATIVE": 0.002,
    "BALANCED": 0.005,
    "AGGRESSIVE": 0.008,
}

# Multiplicadores de magnitud — ver docstring del módulo para el porqué.
_MAGNITUDE_MULTIPLIER = {
    "CONSERVATIVE": 0.5,
    "BALANCED": 1.0,
    "AGGRESSIVE": 1.8,
}

_MAX_POSITION_SIZE_PCT = {
    "CONSERVATIVE": 3.0,
    "BALANCED": 5.0,
    "AGGRESSIVE": 8.0,
}

_SIZING_SCALE = 5.0  # constante de calibración manual — ver test_ev_engine.py para el rango resultante


@dataclass
class EVResult:
    ev_conservative: float
    ev_aggressive: float
    ev_balanced: float
    position_sizing_conservative_pct: float
    position_sizing_aggressive_pct: float
    position_sizing_balanced_pct: float
    threshold_conservative: str
    threshold_aggressive: str
    threshold_balanced: str
    reasoning: str

    def as_json(self) -> dict:
        """Forma exacta del JSON pedido por el spec (Etapa 7)."""
        return {
            "ev_conservative": round(self.ev_conservative * 100, 3),
            "ev_aggressive": round(self.ev_aggressive * 100, 3),
            "ev_balanced": round(self.ev_balanced * 100, 3),
            "position_sizing_conservative": f"{self.position_sizing_conservative_pct:.2f}%",
            "position_sizing_aggressive": f"{self.position_sizing_aggressive_pct:.2f}%",
            "position_sizing_balanced": f"{self.position_sizing_balanced_pct:.2f}%",
            "threshold_conservative": self.threshold_conservative,
            "threshold_aggressive": self.threshold_aggressive,
            "threshold_balanced": self.threshold_balanced,
            "reasoning": self.reasoning,
        }


def _raw_point_estimate(
    net_conviction: float,
    confidence_in_conviction: float,
    expected_magnitude_pct: float,
    impact_confidence: float,
) -> float:
    """Punto central de EV, en fracción (0.01 = 1%), antes de amplificar/amortiguar
    por versión de estrategia.

    net_conviction: -1..1 (del Judge). confidence_in_conviction: 0-100 (del Judge).
    expected_magnitude_pct: magnitud esperada en % de los análogos históricos
        (Etapa 6, historical_analogues.py) — CON SIGNO ya aplicado por el caller
        si expected_direction es fijo, o el caller puede pasar la magnitud sin
        signo y dejar que net_conviction aporte la dirección (ver ev_engine desde
        el orquestador: se usa la magnitud SIN signo de los análogos, y la
        dirección viene del Judge — dos fuentes de dirección independientes que
        podrían discrepar es justamente la clase de conflicto que detecta
        abstention_engine.py como señal de "datos contradictorios").
    impact_confidence: 0-100 (de la Etapa 6) — refleja el tamaño de muestra de
        análogos, no la convicción del Judge. Se multiplica, no se promedia,
        para que un dato flojo en CUALQUIERA de los dos frentes (Judge inseguro
        O pocos análogos) tire el EV hacia 0, en vez de que uno alto compense
        al otro bajo.
    """
    return net_conviction * abs(expected_magnitude_pct) / 100 * (confidence_in_conviction / 100) * (impact_confidence / 100)


def _threshold_check(ev: float, strategy: str) -> str:
    threshold = EV_THRESHOLDS[strategy]
    decision = "TRADE" if ev > threshold else "NO_TRADE"
    return f"EV={ev * 100:.2f}% {'>' if ev > threshold else '<='} {threshold * 100:.1f}% → {decision}"


def _position_size(ev: float, confidence_in_conviction: float, strategy: str) -> float:
    """Tamaño de posición en % de cartera. Escala con |EV| y con la confianza
    del Judge, con un tope duro por versión (nunca se apuesta el máximo solo
    porque el EV puntual sea alto — la confianza también tiene que acompañar)."""
    raw = abs(ev) * 100 * _SIZING_SCALE * (confidence_in_conviction / 100)
    return min(raw, _MAX_POSITION_SIZE_PCT[strategy])


def compute_ev(
    net_conviction: float,
    confidence_in_conviction: float,
    expected_magnitude_pct: float,
    impact_confidence: float,
) -> EVResult:
    """Punto de entrada de la Etapa 7. Ver _raw_point_estimate para el
    significado exacto de cada argumento."""
    raw = _raw_point_estimate(net_conviction, confidence_in_conviction, expected_magnitude_pct, impact_confidence)

    ev_conservative = raw * _MAGNITUDE_MULTIPLIER["CONSERVATIVE"]
    ev_balanced = raw * _MAGNITUDE_MULTIPLIER["BALANCED"]
    ev_aggressive = raw * _MAGNITUDE_MULTIPLIER["AGGRESSIVE"]

    reasoning = (
        f"net_conviction={net_conviction:+.2f} (Judge) × "
        f"expected_magnitude={abs(expected_magnitude_pct):.2f}% (Etapa 6) × "
        f"confidence_in_conviction={confidence_in_conviction:.0f}% × "
        f"impact_confidence={impact_confidence:.0f}% → punto central {raw * 100:+.2f}%. "
        f"Balanced usa el punto central sin amplificar; Conservative lo amortigua "
        f"×{_MAGNITUDE_MULTIPLIER['CONSERVATIVE']}; Aggressive lo amplifica ×{_MAGNITUDE_MULTIPLIER['AGGRESSIVE']}."
    )

    return EVResult(
        ev_conservative=ev_conservative,
        ev_aggressive=ev_aggressive,
        ev_balanced=ev_balanced,
        position_sizing_conservative_pct=_position_size(ev_conservative, confidence_in_conviction, "CONSERVATIVE"),
        position_sizing_aggressive_pct=_position_size(ev_aggressive, confidence_in_conviction, "AGGRESSIVE"),
        position_sizing_balanced_pct=_position_size(ev_balanced, confidence_in_conviction, "BALANCED"),
        threshold_conservative=_threshold_check(ev_conservative, "CONSERVATIVE"),
        threshold_aggressive=_threshold_check(ev_aggressive, "AGGRESSIVE"),
        threshold_balanced=_threshold_check(ev_balanced, "BALANCED"),
        reasoning=reasoning,
    )
