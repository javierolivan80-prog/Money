"""test_config.py — secretos limpios de saltos de línea.

Un secreto de GitHub Actions pegado con un salto de línea al final llega
intacto a os.environ. Ya pasó una vez con DATABASE_URL:

    invalid channel_binding value: "require\\n"

Y otra vez con ANTHROPIC_API_KEY (2026-09-15, run 34958557148), con un fallo
mucho más opaco porque no rompe la conexión sino la petición HTTP: un salto de
línea en una cabecera es el vector clásico de inyección de cabeceras, así que
la librería la rechaza en vez de mandarla:

    httpx2.LocalProtocolError: Illegal header value b'***\\n'

veinte líneas de traza por debajo de "anthropic.APIConnectionError: Connection
error.", que no menciona la cabecera ni el salto de línea en ningún sitio.

Este fichero fija que TODO secreto que cruza esa frontera (variable de
entorno -> valor usado) sale limpio, y que limpiarlo en config.py no basta si
el punto de uso no pasa por config.py.
"""
from __future__ import annotations

import importlib

import pytest


def _recargar_config(monkeypatch, **env):
    """Reimporta pipeline.config con variables de entorno controladas — el
    módulo lee os.environ al importarse, así que hay que forzar la relectura
    en cada test en vez de solo hacer monkeypatch de un atributo."""
    for k in ("DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from pipeline import config
    return importlib.reload(config)


def test_database_url_se_limpia_de_salto_de_linea(monkeypatch):
    config = _recargar_config(monkeypatch, DATABASE_URL="postgresql://x/y?sslmode=require\n")
    assert config.DATABASE_URL == "postgresql://x/y?sslmode=require"


def test_anthropic_api_key_se_limpia_de_salto_de_linea(monkeypatch):
    """EL fallo del run 34958557148: la clave llegaba con un '\\n' final y
    provocaba httpx2.LocalProtocolError al mandarla como cabecera HTTP."""
    config = _recargar_config(monkeypatch, ANTHROPIC_API_KEY="sk-ant-xxxxx\n")
    assert config.ANTHROPIC_API_KEY == "sk-ant-xxxxx"


def test_anthropic_api_key_ausente_es_none_no_cadena_vacia(monkeypatch):
    """None es lo que espera el chequeo `if not config.ANTHROPIC_API_KEY` de
    event_analysis_pipeline.py; una cadena vacía también pasaría ese chequeo,
    así que no hace falta distinguirlas, pero None es más honesto: la clave no
    existe, no es que exista y esté vacía."""
    config = _recargar_config(monkeypatch)
    assert config.ANTHROPIC_API_KEY is None


def test_anthropic_api_key_sin_espacios_no_se_toca(monkeypatch):
    config = _recargar_config(monkeypatch, ANTHROPIC_API_KEY="sk-ant-xxxxx")
    assert config.ANTHROPIC_API_KEY == "sk-ant-xxxxx"


def test_limpiar_en_config_no_basta_si_el_cliente_no_lo_usa():
    """REGRESIÓN DEL FALLO REAL: no basta con limpiar ANTHROPIC_API_KEY en
    config.py si `anthropic.Anthropic()` se sigue llamando SIN argumentos — la
    librería lee la variable de entorno directamente, sin pasar por config.py,
    así que el .strip() de arriba no le llega. Los dos puntos de creación del
    cliente tienen que pasar api_key=config.ANTHROPIC_API_KEY explícitamente."""
    import ast
    import pathlib

    for fichero in ("pipeline/analyze/adversarial_analyzer.py", "pipeline/analyze/event_analysis_pipeline.py"):
        arbol = ast.parse(pathlib.Path(fichero).read_text())
        llamadas = [
            n for n in ast.walk(arbol)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "Anthropic"
        ]
        assert llamadas, f"{fichero}: no se encontró ninguna llamada a Anthropic(...)"
        for llamada in llamadas:
            nombres_kw = {kw.arg for kw in llamada.keywords}
            assert "api_key" in nombres_kw, (
                f"{fichero}: anthropic.Anthropic() sin api_key= explícito — "
                "volvería a leer la variable de entorno sin pasar por el "
                ".strip() de config.py"
            )


@pytest.mark.parametrize("sucio", ["sk-ant-x\n", "sk-ant-x\r\n", " sk-ant-x ", "\tsk-ant-x\t"])
def test_cualquier_espacio_en_blanco_envolvente_se_quita(monkeypatch, sucio):
    config = _recargar_config(monkeypatch, ANTHROPIC_API_KEY=sucio)
    assert config.ANTHROPIC_API_KEY == "sk-ant-x"
