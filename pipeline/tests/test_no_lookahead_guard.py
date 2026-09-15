"""test_no_lookahead_guard.py — red de seguridad contra look-ahead en el pipeline de decisión.

Por qué existe este fichero: el look-ahead del prior de shrinkage
(get_class_prior_mean, corregido en e1f6c61) sobrevivió a 288 tests y a varias
revisiones porque no rompía nada — devolvía un número plausible, solo que
calculado con datos del futuro. Los tests de comportamiento no lo detectan:
hay que mirar el SQL.

Esto lo automatiza. Cualquier query de `pipeline/analyze/` que lea de `events`
o `car_results` tiene que acotarse temporalmente con `d0_close_date`, o estar
en ALLOWLIST con un motivo escrito. Añadir una query nueva sin filtro se
convierte así en un acto consciente (editar la allowlist) en vez de un
descuido silencioso.

Ámbito deliberado: solo `pipeline/analyze/`. Es el camino de DECISIÓN — lo que
corre mientras se decide si operar, con la información que existía en ese
momento. Los módulos de `backtest/` y `validation/` leen resultados ya
cerrados, después del hecho, y acotarlos por d0_close_date no tendría sentido.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ANALYZE_DIR = Path(__file__).resolve().parent.parent / "analyze"

# Tablas cuyo acceso sin acotar en el tiempo es un look-ahead potencial, y la
# columna por la que hay que acotarlas. No es la misma para todas: los eventos
# se conocen al cierre del día en que se presentan (d0_close_date), mientras
# que un ejercicio contable no se conoce hasta que se presenta el 10-K semanas
# después del cierre del período (filed_at, NO fiscal_period_end — ver la
# cabecera de ingest/xbrl_fundamentals.py).
TIME_SENSITIVE_TABLES: dict[str, str] = {
    "events": "d0_close_date",
    "car_results": "d0_close_date",
    "fundamentals": "filed_at",
}

# Predicados que cuentan como acotación temporal válida, por columna.
TEMPORAL_GUARDS: dict[str, re.Pattern] = {
    column: re.compile(rf"{column}\s*(<|<=|BETWEEN|>=|>)", re.IGNORECASE) for column in set(TIME_SENSITIVE_TABLES.values())
}

# Excepciones documentadas. La clave es (fichero, fragmento identificativo de la
# query); el valor es POR QUÉ es seguro. Una entrada sin motivo real aquí es
# exactamente el agujero que este test existe para cerrar — si dudas, no la
# añadas: acota la query.
ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "event_analysis_pipeline.py",
        "LEFT JOIN event_analyses ea ON ea.event_id = e.event_id",
    ): (
        "Cola de trabajo, no una lectura de contexto: selecciona qué eventos "
        "quedan por analizar. No alimenta ninguna estimación — cada evento se "
        "analiza después con su propio as_of_date."
    ),
    (
        "adversarial_analyzer.py",
        "ea.analyzed_at >= now()",
    ): (
        "Caché de 24h de Bull/Bear/Judge, acotada por analyzed_at (reloj real "
        "de cuándo se llamó al modelo), no por la fecha del evento. Es control "
        "de coste de API, no una fuente de información sobre el evento."
    ),
    (
        "adversarial_analyzer.py",
        "WHERE ea.event_id IS NULL LIMIT",
    ): (
        "Bloque __main__ del smoke test manual (--smoke-test), fuera del "
        "camino de producción."
    ),
    (
        "quality_score.py",
        "SELECT * FROM fundamentals WHERE cik = %s ORDER BY fiscal_period_end",
    ): (
        "Rama as_of_date=None de fetch_annual_rows: vista EN VIVO del "
        "dashboard, donde 'todo lo publicado hasta hoy' ES la respuesta "
        "correcta — hoy no tiene futuro del que hacer look-ahead. La rama "
        "con as_of_date (la que usaría un backtest) sí acota por filed_at, y "
        "el docstring de la función advierte explícitamente de que en un "
        "backtest hay que pasar la fecha simulada. Si algún día esto se usa "
        "desde un backtest, esta entrada debe desaparecer y la rama sin "
        "acotar con ella."
    ),
}


def _sql_literals(path: Path) -> list[str]:
    """Extrae literales de cadena que parecen SQL de un módulo Python."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if re.search(r"\bSELECT\b", text, re.IGNORECASE) and re.search(r"\bFROM\b", text, re.IGNORECASE):
                out.append(text)
    return out


def _time_sensitive_tables_read(sql: str) -> list[str]:
    """Qué tablas sensibles al tiempo lee esta query (puede ser más de una)."""
    return [table for table in TIME_SENSITIVE_TABLES if re.search(rf"\b(FROM|JOIN)\s+{table}\b", sql, re.IGNORECASE)]


def _reads_time_sensitive_table(sql: str) -> bool:
    return bool(_time_sensitive_tables_read(sql))


def _is_time_bounded(sql: str) -> bool:
    """Una query está acotada si TODAS las tablas sensibles que lee tienen su
    predicado temporal correspondiente. Basta con que falte el de una para que
    la query pueda colar información del futuro por esa vía."""
    tables = _time_sensitive_tables_read(sql)
    if not tables:
        return True
    return all(TEMPORAL_GUARDS[TIME_SENSITIVE_TABLES[table]].search(sql) for table in tables)


def _allowlist_reason(filename: str, sql: str) -> str | None:
    for (allowed_file, marker), reason in ALLOWLIST.items():
        if allowed_file == filename and marker in sql:
            return reason
    return None


def test_every_decision_path_query_is_time_bounded():
    """Toda lectura de una tabla sensible al tiempo en el camino de decisión se
    acota por su columna correspondiente (ver TIME_SENSITIVE_TABLES), o está
    allowlisted con motivo."""
    violations = []

    for path in sorted(ANALYZE_DIR.glob("*.py")):
        for sql in _sql_literals(path):
            if not _reads_time_sensitive_table(sql):
                continue
            if _is_time_bounded(sql):
                continue
            if _allowlist_reason(path.name, sql):
                continue
            violations.append(f"{path.name}:\n{sql.strip()}")

    assert not violations, (
        "Query(s) sin acotar temporalmente en el camino de decisión.\n\n"
        "Añade el predicado temporal que corresponda a cada tabla "
        f"({TIME_SENSITIVE_TABLES}), o —si de verdad es seguro— una entrada en "
        "ALLOWLIST de este fichero explicando por qué.\n\n"
        + "\n\n---\n\n".join(violations)
    )


def test_allowlist_entries_still_match_real_queries():
    """Una allowlist que se queda obsoleta es peor que no tenerla: silencia
    queries que ya no son las que se auditaron. Si una entrada deja de
    corresponder a SQL real, hay que borrarla."""
    stale = []

    for (filename, marker), _reason in ALLOWLIST.items():
        path = ANALYZE_DIR / filename
        if not path.exists():
            stale.append(f"{filename} ya no existe (marker: {marker!r})")
            continue
        if not any(marker in sql for sql in _sql_literals(path)):
            stale.append(f"{filename}: ningún SQL contiene {marker!r}")

    assert not stale, "Entradas de ALLOWLIST obsoletas:\n" + "\n".join(stale)


def test_guard_detects_an_unbounded_query():
    """El guard tiene que fallar de verdad ante una query sin acotar — si no,
    pasaría en verde para siempre sin comprobar nada."""
    unbounded = "SELECT avg(car) FROM car_results cr JOIN events e ON e.event_id = cr.event_id"
    assert _reads_time_sensitive_table(unbounded)
    assert not _is_time_bounded(unbounded)

    bounded = unbounded + " WHERE e.d0_close_date < %(as_of_date)s"
    assert _is_time_bounded(bounded)


def test_guard_covers_fundamentals_with_its_own_temporal_column():
    """fundamentals es tan sensible al tiempo como events, pero su columna
    anti-look-ahead es filed_at, no d0_close_date: el ejercicio cerrado el
    31-12 no se conoce hasta que se presenta el 10-K semanas después. Acotar
    por fiscal_period_end en vez de por filed_at daría al sistema esas semanas
    de información del futuro — por eso NO cuenta como acotación válida."""
    unbounded = "SELECT * FROM fundamentals WHERE cik = %s"
    assert _reads_time_sensitive_table(unbounded)
    assert not _is_time_bounded(unbounded)

    # fiscal_period_end NO vale como guard: es la fecha del período, no la de
    # publicación — justo la confusión que causa el look-ahead.
    wrong_column = unbounded + " AND fiscal_period_end <= %s"
    assert not _is_time_bounded(wrong_column)

    bounded = unbounded + " AND filed_at <= %s"
    assert _is_time_bounded(bounded)


def test_guard_requires_every_time_sensitive_table_in_a_join_to_be_bounded():
    """Una query que une dos tablas sensibles y solo acota una sigue pudiendo
    colar futuro por la otra."""
    half_bounded = (
        "SELECT * FROM events e JOIN fundamentals f ON f.cik = e.cik "
        "WHERE e.d0_close_date < %(as_of)s"
    )
    assert not _is_time_bounded(half_bounded)

    fully_bounded = half_bounded + " AND f.filed_at <= %(as_of)s"
    assert _is_time_bounded(fully_bounded)


def test_fda_crl_8k_lookup_does_not_look_forward():
    """Regresión: check_fda_crl_without_8k buscaba un 8-K correspondiente en
    una ventana de ±10 días alrededor de D0, es decir, hasta 10 días en el
    FUTURO de la decisión.

    La regla que implementa es "¿la empresa ya ha comunicado esto oficialmente?".
    Esa pregunta solo puede responderse con filings existentes en D0. Con la
    ventana hacia delante, el sistema dejaba de abstenerse justo en los casos
    en que la empresa acabaría confirmando con un 8-K posterior — usando el
    futuro para decidir operar en el presente.
    """
    from pipeline.analyze import event_analysis_pipeline as pipe

    source = Path(pipe.__file__).read_text(encoding="utf-8")
    assert "d0_close_date + timedelta" not in source, (
        "check_fda_crl_without_8k vuelve a mirar hacia delante: la ventana de "
        "búsqueda del 8-K no puede extenderse más allá de d0_close_date."
    )
