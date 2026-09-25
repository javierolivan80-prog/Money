"""fake_batch_api.py — lo que la Batch API real rechaza, comprobado en los
clientes falsos de los tests.

Los dos fallos que impidieron que event_analyses guardase una sola fila
pasaron TODOS los tests porque los clientes falsos aceptaban cualquier cosa:
  - custom_id con dos puntos -> 400 del batch entero (run 34960903955);
  - minimum/maximum en el esquema del Judge -> todas las requests `errored`
    (runs 34964242549 y 34970097017).
Cada cliente falso llama a validar_requests_como_la_api() en su create(), así
que una regresión en cualquier camino que construya un batch falla aquí, no
en producción con saldo real de por medio.
"""
from __future__ import annotations

import re

PATRON_CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Restricciones de JSON Schema que structured outputs NO admite (docs de la
# API: "Numerical constraints", "String constraints", "Complex array
# constraints").
CLAVES_NO_SOPORTADAS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "uniqueItems",
}


def claves_no_soportadas(schema) -> list[str]:
    """Rutas de las claves no soportadas dentro de un esquema (recursivo)."""
    encontradas: list[str] = []

    def _walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in CLAVES_NO_SOPORTADAS:
                    encontradas.append(f"{path}.{k}")
                if k == "additionalProperties" and v is not False:
                    encontradas.append(f"{path}.additionalProperties={v!r}")
                _walk(v, f"{path}.{k}")
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                encontradas.append(f"{path} (object sin additionalProperties: false)")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")

    _walk(schema, "schema")
    return encontradas


def validar_requests_como_la_api(requests) -> None:
    assert requests, "batch vacío: la API real lo rechaza con un 400"
    ids = [r["custom_id"] for r in requests]
    for cid in ids:
        assert PATRON_CUSTOM_ID.match(cid), f"custom_id {cid!r} no cumple {PATRON_CUSTOM_ID.pattern}: la API rechaza el batch entero"
    assert len(ids) == len(set(ids)), "custom_id repetido dentro del batch"
    for r in requests:
        fmt = ((r.get("params") or {}).get("output_config") or {}).get("format") or {}
        if "schema" in fmt:
            malas = claves_no_soportadas(fmt["schema"])
            assert not malas, f"{r['custom_id']}: structured outputs no admite {malas} — la request saldría `errored`"
