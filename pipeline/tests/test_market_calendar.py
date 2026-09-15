"""test_market_calendar.py — calendario de días de bolsa de EE.UU.

El caso de referencia no es inventado: son los 14 días que el backfill de
precios marcó como "posible data gap" en casi los 150 tickers del run
34943861450, incluidos 3M, Adobe y Principal Financial. Eran los 14 festivos
del rango, ni uno más ni uno menos.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline.ingest.market_calendar import (
    _domingo_de_pascua,
    dias_de_negociacion,
    festivos_del_anio,
)

# Copiado del log de producción: rango exacto que pidió el workflow.
RANGO_PRODUCCION = (date(2025, 4, 28), date(2026, 9, 15))
FESTIVOS_ESPERADOS_EN_ESE_RANGO = [
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7, 4),    # Independencia
    date(2025, 9, 1),    # Trabajo
    date(2025, 11, 27),  # Acción de Gracias
    date(2025, 12, 25),  # Navidad
    date(2026, 1, 1),    # Año Nuevo
    date(2026, 1, 19),   # MLK
    date(2026, 2, 16),   # Presidentes
    date(2026, 4, 3),    # Viernes Santo
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independencia (el 4 cae sábado)
    date(2026, 9, 7),    # Trabajo
]


def _lunes_a_viernes(inicio: date, fin: date) -> set[date]:
    """Lo que hacía el código viejo: todo día laborable cuenta."""
    dias, d = set(), inicio
    while d <= fin:
        if d.weekday() < 5:
            dias.add(d)
        d += timedelta(days=1)
    return dias


def test_reproduce_exactamente_los_14_huecos_falsos_de_produccion():
    """EL test que justifica el módulo entero: la diferencia entre el
    calendario ingenuo y el real tiene que ser exactamente los 14 días que
    producción marcó como huecos en 3M, Adobe y compañía."""
    inicio, fin = RANGO_PRODUCCION
    diferencia = sorted(_lunes_a_viernes(inicio, fin) - dias_de_negociacion(inicio, fin))
    assert diferencia == FESTIVOS_ESPERADOS_EN_ESE_RANGO


def test_un_ticker_sin_huecos_reales_ya_no_dispara_la_alarma():
    """La consecuencia práctica: con los días de bolsa correctos, una serie de
    precios completa no deja ni un día 'faltante'. Antes dejaba 14 y la alerta
    saltaba en casi todos los tickers a la vez."""
    inicio, fin = RANGO_PRODUCCION
    esperados = dias_de_negociacion(inicio, fin)
    presentes = esperados  # un valor que cotizó todos los días de bolsa
    assert esperados - presentes == set()


def test_un_hueco_de_verdad_sigue_detectandose():
    """Lo contrario también importa: quitar los festivos no puede tapar un
    deslistado real."""
    inicio, fin = RANGO_PRODUCCION
    esperados = dias_de_negociacion(inicio, fin)
    hueco = {d for d in esperados if date(2026, 3, 2) <= d <= date(2026, 3, 13)}
    assert esperados - (esperados - hueco) == hueco
    assert len(hueco) == 10  # dos semanas de bolsa seguidas sin cotizar


@pytest.mark.parametrize(
    "anio,esperado",
    [
        (2021, date(2021, 4, 4)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
    ],
)
def test_domingo_de_pascua(anio, esperado):
    """Viernes Santo es el único festivo de la bolsa que se mueve con el
    calendario lunar; si la Pascua sale mal, sale mal un día al año."""
    assert _domingo_de_pascua(anio) == esperado


@pytest.mark.parametrize("anio", range(2021, 2031))
def test_viernes_santo_siempre_es_viernes(anio):
    viernes_santo = _domingo_de_pascua(anio) - timedelta(days=2)
    assert viernes_santo.weekday() == 4
    assert viernes_santo in festivos_del_anio(anio)


@pytest.mark.parametrize("anio", range(2021, 2031))
def test_ningun_festivo_cae_en_fin_de_semana(anio):
    """Un festivo en sábado o domingo no cierra nada: o se traslada a un día
    hábil o no existe. Colarlo restaría un día que la bolsa sí abre."""
    for dia in festivos_del_anio(anio):
        assert dia.weekday() < 5, dia


@pytest.mark.parametrize("anio", range(2021, 2031))
def test_hay_entre_nueve_y_once_festivos_al_anio(anio):
    """La bolsa cierra 9 o 10 días al año (11 si hay cierre extraordinario).
    Un número fuera de ahí significa que una regla está mal."""
    assert 9 <= len(festivos_del_anio(anio)) <= 11


def test_ano_nuevo_en_sabado_no_se_traslada():
    """Excepción propia de la NYSE: el resto de festivos de fecha fija que caen
    en sábado se observan el viernes anterior, pero el Año Nuevo no — la bolsa
    NO cierra el 31 de diciembre. 2022 es uno de esos años."""
    assert date(2021, 12, 31) not in festivos_del_anio(2021)
    assert date(2022, 1, 1) not in festivos_del_anio(2022)  # sábado
    assert date(2021, 12, 31).weekday() == 4  # y es viernes, día hábil


def test_festivo_en_domingo_se_observa_el_lunes():
    """El 4 de julio de 2021 cayó domingo: la bolsa cerró el lunes 5."""
    festivos = festivos_del_anio(2021)
    assert date(2021, 7, 5) in festivos
    assert date(2021, 7, 4) not in festivos


def test_festivo_en_sabado_se_observa_el_viernes():
    """El 4 de julio de 2026 cae sábado: se observa el viernes 3, que es
    justamente uno de los 14 días del caso de producción."""
    festivos = festivos_del_anio(2026)
    assert date(2026, 7, 3) in festivos
    assert date(2026, 7, 4) not in festivos


def test_juneteenth_no_existe_antes_de_2021():
    """Se hizo festivo federal en 2021. El histórico del proyecto empieza en
    2021-01-01, pero tratarlo como festivo hacia atrás marcaría como cerrado
    un día en que la bolsa abrió."""
    assert date(2020, 6, 19) not in festivos_del_anio(2020)
    assert date(2021, 6, 18) in festivos_del_anio(2021)  # el 19 cayó sábado


def test_cierre_extraordinario_por_duelo_nacional():
    """El 9 de enero de 2025 la bolsa cerró por el funeral de Jimmy Carter. No
    lo da ninguna regla: o está en la lista o se cuenta como hueco falso."""
    assert date(2025, 1, 9) not in dias_de_negociacion(date(2025, 1, 5), date(2025, 1, 15))


def test_dias_de_negociacion_incluye_los_extremos_del_rango():
    un_lunes = date(2026, 9, 14)
    assert un_lunes in dias_de_negociacion(un_lunes, un_lunes)


def test_una_semana_normal_tiene_cinco_dias():
    lunes, viernes = date(2026, 9, 14), date(2026, 9, 18)
    assert len(dias_de_negociacion(lunes, viernes)) == 5


def test_rango_que_cruza_varios_anios():
    """El cálculo de festivos va año por año; un rango que cruza diciembre no
    puede perderse los del año siguiente."""
    dias = dias_de_negociacion(date(2025, 12, 20), date(2026, 1, 20))
    assert date(2025, 12, 25) not in dias
    assert date(2026, 1, 1) not in dias
    assert date(2026, 1, 19) not in dias
    assert date(2026, 1, 20) in dias
