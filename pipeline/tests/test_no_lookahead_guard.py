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

# Tablas cuyo acceso sin acotar en el tiempo es un look-ahead potencial.
TIME_SENSITIVE_TABLES = ("events", "car_results")

# Predicados que cuentan como acotación temporal válida.
TEMPORAL_GUARD = re.compile(r"d0_close_date\s*(<|<=|BETWEEN|>=|>)", re.IGNORECASE)

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


def _reads_time_sensitive_table(sql: str) -> bool:
    for table in TIME_SENSITIVE_TABLES:
        if re.search(rf"\b(FROM|JOIN)\s+{table}\b", sql, re.IGNORECASE):
            return True
    return False


def _allowlist_reason(filename: str, sql: str) -> str | None:
    for (allowed_file, marker), reason in ALLOWLIST.items():
        if allowed_file == filename and marker in sql:
            return reason
    return None


def test_every_decision_path_query_is_time_bounded():
    """Toda lectura de events/car_results en el camino de decisión se acota por
    d0_close_date, o está allowlisted con motivo."""
    violations = []

    for path in sorted(ANALYZE_DIR.glob("*.py")):
        for sql in _sql_literals(path):
            if not _reads_time_sensitive_table(sql):
                continue
            if TEMPORAL_GUARD.search(sql):
                continue
            if _allowlist_reason(path.name, sql):
                continue
            violations.append(f"{path.name}:\n{sql.strip()}")

    assert not violations, (
        "Query(s) sin acotar temporalmente en el camino de decisión.\n\n"
        "Añade un predicado sobre d0_close_date, o —si de verdad es seguro— una "
        "entrada en ALLOWLIST de este fichero explicando por qué.\n\n"
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
    assert not TEMPORAL_GUARD.search(unbounded)

    bounded = unbounded + " WHERE e.d0_close_date < %(as_of_date)s"
    assert TEMPORAL_GUARD.search(bounded)


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
