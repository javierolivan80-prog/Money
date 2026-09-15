"""test_quality_score.py — análisis fundamental de largo plazo.

Funciones puras con números a mano (mismo patrón que test_ev_engine.py y
test_event_study.py): un caso claro por criterio, más los casos de datos
ausentes, que son los que de verdad importan aquí — la nota no puede
penalizar a una empresa por no reportar una magnitud.
"""
import pytest

from pipeline.analyze.quality_score import (
    MIN_YEARS_FOR_GROWTH,
    compute_quality_score,
)


def _row(**overrides) -> dict:
    """Ejercicio de una empresa sana, para sobreescribir campo a campo."""
    base = {
        "revenue": 1_000_000.0,
        "net_income": 150_000.0,
        "stockholders_equity": 750_000.0,   # ROE = 20%
        "total_assets": 1_500_000.0,
        "total_liabilities": 750_000.0,
        "long_term_debt": 200_000.0,         # deuda/equity = 0.27
        "operating_cash_flow": 180_000.0,
        "capex": 20_000.0,                   # FCF = 160k -> 1.07x el beneficio
        "shares_outstanding": 100_000.0,
    }
    base.update(overrides)
    return base


def _growing_history(n_years: int, growth: float = 0.12) -> list[dict]:
    rows = []
    for i in range(n_years):
        revenue = 1_000_000.0 * ((1 + growth) ** i)
        rows.append(_row(revenue=revenue))
    return rows


# ---------------------------------------------------------------------------
# Caso completo
# ---------------------------------------------------------------------------


def test_empresa_sana_puntua_alto():
    result = compute_quality_score(_growing_history(5), current_price=10.0)  # PER = 1M/150k = 6.7
    assert result.total is not None
    assert result.total > 75
    assert result.unavailable == []
    assert "sólido" in result.verdict.lower()


def test_empresa_endeudada_y_sin_crecimiento_puntua_bajo():
    rows = [_row(revenue=1_000_000.0 * (0.9**i), net_income=5_000.0, long_term_debt=2_000_000.0) for i in range(5)]
    result = compute_quality_score(rows, current_price=100.0)
    assert result.total is not None
    assert result.total < 40


# ---------------------------------------------------------------------------
# Componentes individuales
# ---------------------------------------------------------------------------


def _component(result, name):
    return next(c for c in result.components if c.name == name)


def test_rentabilidad_usa_roe_del_ultimo_ejercicio():
    rows = _growing_history(4)
    rows[-1] = _row(net_income=75_000.0, stockholders_equity=750_000.0)  # ROE = 10%
    result = compute_quality_score(rows, current_price=10.0)
    assert _component(result, "Rentabilidad").raw_value == pytest.approx(0.10)


def test_solidez_sin_deuda_declarada_cuenta_como_cero_deuda():
    """long_term_debt=None no es 'no se sabe': en XBRL, una empresa sin deuda
    a largo plazo simplemente no reporta la etiqueta."""
    rows = [_row(long_term_debt=None) for _ in range(4)]
    result = compute_quality_score(rows, current_price=10.0)
    solidez = _component(result, "Solidez financiera")
    assert solidez.raw_value == pytest.approx(0.0)
    assert solidez.score == 100.0


def test_calidad_beneficio_detecta_beneficio_que_no_es_caja():
    rows = [_row(net_income=200_000.0, operating_cash_flow=60_000.0, capex=20_000.0) for _ in range(4)]
    result = compute_quality_score(rows, current_price=10.0)
    calidad = _component(result, "Calidad del beneficio")
    assert calidad.raw_value == pytest.approx(0.2)  # FCF 40k / beneficio 200k
    assert calidad.score is not None and calidad.score < 20
    assert "solo entran" in calidad.explanation


def test_crecimiento_cagr_calculado_a_mano():
    # 1.000.000 -> 1.331.000 en 3 saltos = 10% anual exacto
    rows = [_row(revenue=r) for r in (1_000_000.0, 1_100_000.0, 1_210_000.0, 1_331_000.0)]
    result = compute_quality_score(rows, current_price=10.0)
    assert _component(result, "Crecimiento").raw_value == pytest.approx(0.10)


def test_precio_per_calculado_a_mano():
    rows = _growing_history(4)
    # 100.000 acciones a 30€ = 3M de capitalización / 150k de beneficio = PER 20
    result = compute_quality_score(rows, current_price=30.0)
    assert _component(result, "Precio").raw_value == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Datos ausentes — el comportamiento que más importa
# ---------------------------------------------------------------------------


def test_sin_precio_el_componente_precio_queda_no_disponible_y_se_renormaliza():
    """No pasar precio no debe hundir la nota: el criterio se excluye y los
    otros cuatro se reparten el 100%.

    Se usa un precio CARO a propósito: con un precio barato los cinco
    criterios saturarían en 100 y el test no distinguiría entre 'renormaliza'
    y 'puntúa el precio como perfecto'.
    """
    rows = _growing_history(5)
    # 100.000 acciones a 60€ = 6M / 150k de beneficio = PER 40 -> caro.
    con_precio_caro = compute_quality_score(rows, current_price=60.0)
    sin_precio = compute_quality_score(rows, current_price=None)

    assert _component(sin_precio, "Precio").score is None
    assert sin_precio.unavailable == ["Precio"]
    assert _component(con_precio_caro, "Precio").score == pytest.approx(0.0)

    # El precio caro SÍ baja la nota cuando está disponible...
    assert con_precio_caro.total is not None and con_precio_caro.total < 90
    # ...y al no estar disponible no se penaliza: se reparte entre los otros
    # cuatro, que siguen siendo buenos.
    assert sin_precio.total is not None and sin_precio.total > con_precio_caro.total


def test_pocos_ejercicios_dejan_crecimiento_no_disponible():
    rows = _growing_history(MIN_YEARS_FOR_GROWTH - 1)
    result = compute_quality_score(rows, current_price=10.0)
    crecimiento = _component(result, "Crecimiento")
    assert crecimiento.score is None
    assert "al menos" in crecimiento.explanation


def test_empresa_en_perdidas_deja_precio_no_disponible_pero_no_rompe():
    """Sin beneficio no hay PER — pero el resto de criterios sí se calculan."""
    rows = [_row(net_income=-50_000.0) for _ in range(4)]
    result = compute_quality_score(rows, current_price=10.0)
    assert _component(result, "Precio").score is None
    assert _component(result, "Rentabilidad").score is not None
    assert result.total is not None


def test_fondos_propios_negativos_no_revientan_el_calculo():
    rows = [_row(stockholders_equity=-100_000.0) for _ in range(4)]
    result = compute_quality_score(rows, current_price=10.0)
    assert _component(result, "Rentabilidad").score is None
    assert _component(result, "Solidez financiera").score is None
    assert result.total is not None  # crecimiento y precio siguen disponibles


def test_sin_ejercicios_devuelve_nota_none_sin_lanzar():
    result = compute_quality_score([], current_price=10.0)
    assert result.total is None
    assert result.n_years == 0
    assert "Sin datos" in result.verdict


def test_verdict_avisa_cuando_se_apoya_en_pocos_criterios():
    """Una nota calculada sobre 2 de 5 criterios no debe presentarse con la
    misma autoridad que una calculada sobre los 5."""
    rows = [_row(stockholders_equity=-1.0, net_income=None, operating_cash_flow=None)]
    result = compute_quality_score(rows, current_price=None)
    disponibles = [c for c in result.components if c.score is not None]
    assert len(disponibles) < 3
    assert "reservas" in result.verdict.lower() or "Sin datos" in result.verdict


def test_as_json_es_serializable_y_conserva_explicaciones():
    import json

    result = compute_quality_score(_growing_history(4), current_price=10.0)
    payload = result.as_json()
    json.dumps(payload)  # no debe lanzar
    assert len(payload["components"]) == 5
    assert all(c["explanation"] for c in payload["components"])


# --- Decimal de Postgres ----------------------------------------------------


def test_las_columnas_numeric_llegan_como_float_y_no_como_decimal():
    """BUG REAL (run 34943861450): Postgres devuelve NUMERIC como
    decimal.Decimal y este módulo está escrito para float. La mezcla revienta:

        TypeError: unsupported operand type(s) for ** or pow():
                   'decimal.Decimal' and 'float'

    en (last / first) ** (1 / years). Se convierte en la frontera de lectura,
    no en cada operación."""
    from decimal import Decimal

    from pipeline.analyze.quality_score import _a_float

    fila = _a_float({"revenue": Decimal("1000.50"), "cik": "123", "filed_at": None})
    assert isinstance(fila["revenue"], float)
    assert fila["revenue"] == 1000.50
    assert fila["cik"] == "123"      # lo que no es Decimal no se toca
    assert fila["filed_at"] is None


def test_el_calculo_de_crecimiento_aguanta_valores_de_postgres():
    """El caso exacto que petó: cuatro ejercicios de ingresos leídos de la base
    de datos como Decimal."""
    from decimal import Decimal

    from pipeline.analyze.quality_score import _a_float, _score_crecimiento

    filas = [_a_float({"revenue": Decimal(v)}) for v in ("1000", "1100", "1210", "1331")]
    componente = _score_crecimiento([f["revenue"] for f in filas])
    assert componente.score is not None
    assert componente.raw_value == pytest.approx(0.10, abs=0.001)  # 10% anual


def test_calidad_del_beneficio_aguanta_un_capex_ausente():
    """Otro sitio donde la mezcla habría petado más adelante:
    `operating_cash_flow - 0.0` con un Decimal a la izquierda."""
    from decimal import Decimal

    from pipeline.analyze.quality_score import _a_float, _score_calidad_beneficio

    fila = _a_float({"operating_cash_flow": Decimal("150"), "net_income": Decimal("100")})
    componente = _score_calidad_beneficio(fila["operating_cash_flow"], None, fila["net_income"])
    assert componente.score is not None
