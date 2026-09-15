"""market_calendar.py — días de negociación reales de la bolsa de EE.UU.

POR QUÉ EXISTE (bug real, 2026-09-15): el backfill de precios daba por
esperado todo lunes-viernes, sin festivos, y marcaba como "posible data gap"
cualquier día sin cotización. Resultado en el primer run que llegó a descargar
precios de verdad:

    WARNING — posible data gap: MMM tiene 14 días faltantes dentro de su rango
    WARNING — posible data gap: ADBE tiene 14 días faltantes dentro de su rango
    WARNING — posible data gap: PFG tiene 14 días faltantes dentro de su rango
    ... (lo mismo en casi los 150 tickers)

Son 3M, Adobe, Principal Financial: empresas enormes que cotizan todos los
días hábiles. Los 14 "huecos" eran los 14 festivos de la bolsa del rango
2025-04-28 a 2026-09-15, ni uno más ni uno menos.

El daño no es el ruido en el log: survivorship_warning existe para detectar
deslistados y huecos REALES, y se guarda en la tabla de precios como una fila
con precio NULL. Marcando los festivos, la señal se dispara en todos los
tickers a la vez y deja de distinguir nada — un hueco de verdad queda
enterrado entre catorce falsos.

Las reglas están fijadas por la propia NYSE y son estables; se calculan en vez
de listarse año por año para que el calendario no caduque. Los cierres
extraordinarios (funerales de Estado, huracán Sandy) sí van en una lista:
no siguen ninguna regla.
"""
from __future__ import annotations

from datetime import date, timedelta

# Cierres que no obedecen a ninguna regla y hay que saberlos de memoria.
# Solo los que caen dentro del histórico que usa el proyecto (BACKTEST_START
# = 2021-01-01 en adelante).
CIERRES_EXTRAORDINARIOS = {
    date(2025, 1, 9),  # duelo nacional por Jimmy Carter
}


def _domingo_de_pascua(anio: int) -> date:
    """Algoritmo gregoriano anónimo. Hace falta para el Viernes Santo, que es
    el único festivo de la bolsa que se mueve con el calendario lunar."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    mes, dia = divmod(h + ll - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def _enesimo_dia_de_semana(anio: int, mes: int, dia_semana: int, n: int) -> date:
    """n-ésimo <dia_semana> del mes (lunes=0). n=-1 para el último."""
    if n == -1:
        d = date(anio, mes, 1) + timedelta(days=31)
        d = date(d.year, d.month, 1) - timedelta(days=1)  # último día del mes
        while d.weekday() != dia_semana:
            d -= timedelta(days=1)
        return d
    d = date(anio, mes, 1)
    while d.weekday() != dia_semana:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _observado(dia: date) -> date | None:
    """Regla de traslado de la NYSE: si cae en sábado se observa el viernes
    anterior; si cae en domingo, el lunes siguiente."""
    if dia.weekday() == 5:
        return dia - timedelta(days=1)
    if dia.weekday() == 6:
        return dia + timedelta(days=1)
    return dia


def festivos_del_anio(anio: int) -> set[date]:
    """Los días que la bolsa de EE.UU. cierra en un año dado."""
    festivos: set[date] = set()

    # Año Nuevo. Excepción de la NYSE: si el 1 de enero cae en SÁBADO no se
    # traslada al viernes anterior (no se cierra el último día hábil del año
    # anterior), a diferencia del resto de festivos de fecha fija.
    anio_nuevo = date(anio, 1, 1)
    if anio_nuevo.weekday() != 5:
        festivos.add(_observado(anio_nuevo))

    festivos.add(_enesimo_dia_de_semana(anio, 1, 0, 3))    # MLK: 3er lunes de enero
    festivos.add(_enesimo_dia_de_semana(anio, 2, 0, 3))    # Presidentes: 3er lunes de febrero
    festivos.add(_domingo_de_pascua(anio) - timedelta(days=2))  # Viernes Santo
    festivos.add(_enesimo_dia_de_semana(anio, 5, 0, -1))   # Memorial: último lunes de mayo

    # Juneteenth es festivo federal (y de la bolsa) solo desde 2021.
    if anio >= 2021:
        festivos.add(_observado(date(anio, 6, 19)))

    festivos.add(_observado(date(anio, 7, 4)))             # Independencia
    festivos.add(_enesimo_dia_de_semana(anio, 9, 0, 1))    # Trabajo: 1er lunes de septiembre
    festivos.add(_enesimo_dia_de_semana(anio, 11, 3, 4))   # Acción de Gracias: 4º jueves
    festivos.add(_observado(date(anio, 12, 25)))           # Navidad

    festivos |= {d for d in CIERRES_EXTRAORDINARIOS if d.year == anio}
    # Un festivo trasladado nunca puede caer en fin de semana.
    return {d for d in festivos if d.weekday() < 5}


def dias_de_negociacion(inicio: date, fin: date) -> set[date]:
    """Días hábiles de bolsa entre dos fechas, ambas incluidas."""
    festivos: set[date] = set()
    for anio in range(inicio.year, fin.year + 1):
        festivos |= festivos_del_anio(anio)

    dias = set()
    d = inicio
    while d <= fin:
        if d.weekday() < 5 and d not in festivos:
            dias.add(d)
        d += timedelta(days=1)
    return dias
