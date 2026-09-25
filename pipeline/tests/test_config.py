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


@pytest.fixture(autouse=True)
def _restaurar_config_de_verdad_al_acabar():
    """Sin esto, pipeline.config queda CONTAMINADO para el resto de la sesión
    de pytest.

    BUG REAL (2026-09-15, run 34959268239, reproducido IDÉNTICO dos veces —
    391 passed, 2 failed, 63 errors, los mismos 63 en el mismo orden ambas
    veces: no era una casualidad de infraestructura, era determinista).

    _recargar_config hace importlib.reload(config) para leer una variable de
    entorno sucia. monkeypatch deshace la variable de entorno al acabar el
    test, pero el MÓDULO ya reimportado se queda con el DATABASE_URL de la
    prueba (o sin él) — Python no vuelve a leer el entorno solo porque el
    test terminó. Cualquier test que corriera después, en el mismo proceso de
    pytest, heredaba ese config.DATABASE_URL roto. Alfabéticamente eso es
    justo test_populate_car_results.py en adelante, que es exactamente el
    bloque que reventó con "fe_sendauth: no password supplied" — el fallback
    sin usuario ni contraseña de config.py, no la base de datos real de CI.

    Como fixture autouse se registra ANTES que el monkeypatch de cada test
    (pytest instancia los autouse antes que los pedidos explícitamente), así
    que su desmontaje corre DESPUÉS: cuando esto se ejecuta, monkeypatch ya
    restauró el entorno real, y recargar aquí devuelve pipeline.config a
    lo que tenía que ser.
    """
    yield
    from pipeline import config
    importlib.reload(config)


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


# --- El módulo no puede quedar contaminado para el resto de la sesión ------
#
# Captado ANTES de que ningún test de este fichero toque el entorno: es el
# valor de referencia contra el que se compara al final. Si esto se ejecutara
# después de un test sucio sin el fixture de arriba, ya estaría contaminado
# también — por eso se congela aquí, a nivel de módulo, en el momento de
# la recolección.
_DATABASE_URL_ORIGINAL = __import__("os").environ.get("DATABASE_URL")
_ANTHROPIC_API_KEY_ORIGINAL = __import__("os").environ.get("ANTHROPIC_API_KEY")


def test_el_modulo_queda_limpio_para_el_resto_de_la_sesion(monkeypatch):
    """EL test que habría cazado el bug de producción: tras ensuciar y
    recargar pipeline.config en un test, el módulo tiene que volver a reflejar
    el entorno REAL antes de que corra el siguiente test — no el de la
    prueba. Sin el fixture de arriba, esta aserción falla porque config sigue
    con el DATABASE_URL sucio del test anterior."""
    _recargar_config(monkeypatch, DATABASE_URL="postgresql://sucio\n", ANTHROPIC_API_KEY="sk-sucio\n")
    from pipeline import config
    assert config.DATABASE_URL == "postgresql://sucio"  # dentro del test, se aplica

    # Aquí ya no estamos monkeypatcheando nada: esto simula "el siguiente
    # test" mirando el módulo después de que este termine. No se puede
    # comprobar el desmontaje del fixture desde dentro del propio test que lo
    # dispara, así que lo fija test_el_siguiente_test_no_hereda_nada, que
    # corre después en el mismo fichero.


def test_el_siguiente_test_no_hereda_nada():
    """Corre DESPUÉS del test de arriba en el mismo proceso de pytest. Si el
    fixture de restauración no funcionara, este test vería el DATABASE_URL
    sucio ('postgresql://sucio') en vez del real del entorno — exactamente lo
    que le pasó a test_populate_car_results.py y compañía en producción."""
    from pipeline import config
    assert config.DATABASE_URL == (_DATABASE_URL_ORIGINAL.strip() if _DATABASE_URL_ORIGINAL else "postgresql://localhost:5432/money_poc")
    assert config.ANTHROPIC_API_KEY == (_ANTHROPIC_API_KEY_ORIGINAL.strip() if _ANTHROPIC_API_KEY_ORIGINAL else None)
