"""test_xbrl_fundamentals.py — parseo de companyfacts de la SEC.

Lo que de verdad se prueba aquí es la DISCIPLINA DE FECHAS: que `filed_at`
sale de la fecha de presentación y nunca del cierre del ejercicio, y que un
restatement posterior no reescribe hacia atrás lo que se sabía en su momento.
El resto (etiquetas alternativas, campos ausentes) es mecánica de parseo.

Sin red: data.sec.gov está bloqueado en este sandbox (AUDIT_LEAN.md §1.5).
Los fixtures replican la forma documentada de la API.
"""
from datetime import date

import pytest

from pipeline.ingest.xbrl_fundamentals import parse_company_facts


def _fact(end: str, filed: str, val: float, form: str = "10-K", fp: str = "FY") -> dict:
    return {"end": end, "filed": filed, "val": val, "form": form, "fp": fp, "accn": "0000000000-00-000000"}


def _facts(**concepts_by_tag) -> dict:
    """Construye un companyfacts con las etiquetas us-gaap que se le pasen."""
    us_gaap = {}
    for tag, entries in concepts_by_tag.items():
        unit = "shares" if "Shares" in tag else "USD"
        us_gaap[tag] = {"label": tag, "units": {unit: entries}}
    return {"cik": 320193, "entityName": "Test Corp", "facts": {"us-gaap": us_gaap}}


# ---------------------------------------------------------------------------
# Disciplina de fechas — el motivo por el que existe este fichero
# ---------------------------------------------------------------------------


def test_filed_at_es_la_fecha_de_presentacion_no_el_cierre_del_ejercicio():
    """El ejercicio cerrado el 31-12-2023 no se conoce hasta que se presenta el
    10-K en febrero de 2024. Confundir ambas fechas daría al sistema seis
    semanas de futuro en cada ejercicio."""
    facts = _facts(
        Revenues=[_fact("2023-12-31", "2024-02-15", 1_000_000.0)],
        NetIncomeLoss=[_fact("2023-12-31", "2024-02-15", 100_000.0)],
    )
    rows = parse_company_facts(facts)
    assert len(rows) == 1
    assert rows[0]["fiscal_period_end"] == date(2023, 12, 31)
    assert rows[0]["filed_at"] == date(2024, 2, 15)
    assert rows[0]["filed_at"] > rows[0]["fiscal_period_end"]


def test_un_restatement_posterior_no_reescribe_lo_que_se_sabia_entonces():
    """Si la empresa reexpresa 2023 en un filing de 2025, la cifra buena para
    cualquier decisión tomada en 2024 sigue siendo la presentada en 2024. Se
    conserva la presentación MÁS TEMPRANA del período."""
    facts = _facts(
        Revenues=[
            _fact("2023-12-31", "2024-02-15", 1_000_000.0),   # original
            _fact("2023-12-31", "2025-03-01", 900_000.0),      # reexpresado
        ],
        NetIncomeLoss=[_fact("2023-12-31", "2024-02-15", 100_000.0)],
    )
    rows = parse_company_facts(facts)
    assert len(rows) == 1
    assert rows[0]["revenue"] == 1_000_000.0  # la original, no la reexpresada
    assert rows[0]["filed_at"] == date(2024, 2, 15)


def test_filed_at_de_la_fila_es_la_magnitud_mas_lenta():
    """Una fila no está completa hasta que lo está su dato más tardío. Tomar la
    fecha más temprana dejaría la fila disponible antes de que todos sus campos
    existieran — look-ahead dentro de la propia fila."""
    facts = _facts(
        Revenues=[_fact("2023-12-31", "2024-02-15", 1_000_000.0)],
        NetIncomeLoss=[_fact("2023-12-31", "2024-03-20", 100_000.0)],  # más tardío
    )
    rows = parse_company_facts(facts)
    assert rows[0]["filed_at"] == date(2024, 3, 20)


# ---------------------------------------------------------------------------
# Selección de ejercicios y etiquetas
# ---------------------------------------------------------------------------


def test_solo_ejercicios_anuales_de_10k():
    """Los trimestrales (10-Q) y los períodos no anuales dentro de un 10-K se
    descartan: para juzgar un negocio a largo plazo el ruido trimestral
    estorba, y el 10-K es el documento auditado."""
    facts = _facts(
        Revenues=[
            _fact("2023-12-31", "2024-02-15", 1_000_000.0, form="10-K", fp="FY"),
            _fact("2023-06-30", "2023-08-01", 480_000.0, form="10-Q", fp="Q2"),
            _fact("2023-09-30", "2023-11-01", 250_000.0, form="10-K", fp="Q3"),
        ],
    )
    rows = parse_company_facts(facts)
    assert len(rows) == 1
    assert rows[0]["fiscal_period_end"] == date(2023, 12, 31)


def test_usa_la_primera_etiqueta_disponible_de_la_lista_de_fallback():
    """Los ingresos aparecen bajo nombres distintos según sector y año. Gana la
    primera de la lista que exista; no se suman entre sí."""
    facts = _facts(
        RevenueFromContractWithCustomerExcludingAssessedTax=[_fact("2023-12-31", "2024-02-15", 700_000.0)],
        Revenues=[_fact("2023-12-31", "2024-02-15", 999_999.0)],
    )
    rows = parse_company_facts(facts)
    assert rows[0]["revenue"] == 700_000.0  # la primera de _CONCEPT_TAGS, no la otra


def test_magnitud_ausente_queda_none_y_no_se_imputa():
    facts = _facts(Revenues=[_fact("2023-12-31", "2024-02-15", 1_000_000.0)])
    rows = parse_company_facts(facts)
    assert rows[0]["revenue"] == 1_000_000.0
    assert rows[0]["net_income"] is None
    assert rows[0]["long_term_debt"] is None
    assert rows[0]["operating_cash_flow"] is None


def test_varios_ejercicios_salen_ordenados_del_mas_antiguo_al_mas_reciente():
    facts = _facts(
        Revenues=[
            _fact("2023-12-31", "2024-02-15", 1_200_000.0),
            _fact("2021-12-31", "2022-02-15", 1_000_000.0),
            _fact("2022-12-31", "2023-02-15", 1_100_000.0),
        ],
    )
    rows = parse_company_facts(facts)
    assert [r["fiscal_period_end"].year for r in rows] == [2021, 2022, 2023]
    assert [r["revenue"] for r in rows] == [1_000_000.0, 1_100_000.0, 1_200_000.0]


def test_ejercicio_sin_ninguna_magnitud_utilizable_se_descarta():
    """Una entrada sin val/filed no puede generar una fila: sin fecha de
    publicación no hay forma de saber cuándo se supo, y filed_at es NOT NULL."""
    facts = _facts(
        Revenues=[
            {"end": "2023-12-31", "filed": None, "val": 1_000_000.0, "form": "10-K", "fp": "FY"},
        ],
    )
    assert parse_company_facts(facts) == []


def test_companyfacts_sin_cik_lanza():
    with pytest.raises(ValueError, match="cik"):
        parse_company_facts({"entityName": "Sin CIK", "facts": {}})


def test_empresa_sin_hechos_us_gaap_devuelve_lista_vacia():
    assert parse_company_facts({"cik": 123, "facts": {}}) == []


def test_cik_se_guarda_como_texto_para_casar_con_universe():
    """universe.cik es TEXT; el JSON de la SEC trae el CIK como entero."""
    facts = _facts(Revenues=[_fact("2023-12-31", "2024-02-15", 1.0)])
    rows = parse_company_facts(facts)
    assert rows[0]["cik"] == "320193"
    assert isinstance(rows[0]["cik"], str)


# --- CIK con ceros a la izquierda -------------------------------------------
#
# Run 34943861450: universe guarda '1119190' (como viene del daily-index) y la
# API de companyfacts devuelve '0001119190'. Misma empresa, dos cadenas
# distintas para Postgres, y el CIK es clave ajena contra universe:
#
#     violates foreign key constraint "fundamentals_cik_fkey"
#     DETAIL: Key (cik)=(0001119190) is not present in table "universe".


@pytest.mark.parametrize(
    "entrada",
    ["0001119190", "1119190", 1119190, "CIK0001119190", " 0001119190 "],
)
def test_normalizar_cik_deja_todas_las_formas_iguales(entrada):
    from pipeline.ingest.xbrl_fundamentals import normalizar_cik

    assert normalizar_cik(entrada) == "1119190"


def test_normalizar_cik_rechaza_lo_que_no_es_un_cik():
    from pipeline.ingest.xbrl_fundamentals import normalizar_cik

    with pytest.raises(ValueError, match="formato inesperado"):
        normalizar_cik("no-soy-un-cik")


def test_las_filas_salen_con_el_cik_sin_rellenar():
    """EL fallo: la API devuelve el CIK rellenado a 10 dígitos y así entraba en
    la tabla, rompiendo la clave ajena contra universe."""
    from pipeline.ingest.xbrl_fundamentals import parse_company_facts

    facts = _facts(
        Revenues=[_fact("2023-12-31", "2024-02-15", 1_000_000.0)],
        NetIncomeLoss=[_fact("2023-12-31", "2024-02-15", 100_000.0)],
    )
    facts["cik"] = "0001119190"  # como lo devuelve companyfacts de verdad
    for fila in parse_company_facts(facts):
        assert fila["cik"] == "1119190"


def test_manda_el_cik_con_el_que_se_pidio_la_descarga():
    """universe es la fuente de la verdad para el CIK: es la clave ajena contra
    la que se inserta. Si la API contesta con otro, se usa el de universe."""
    from pipeline.ingest.xbrl_fundamentals import parse_company_facts

    facts = _facts(
        Revenues=[_fact("2023-12-31", "2024-02-15", 1_000_000.0)],
        NetIncomeLoss=[_fact("2023-12-31", "2024-02-15", 100_000.0)],
    )
    facts["cik"] = "0009999999"  # la API contesta con otro CIK
    for fila in parse_company_facts(facts, cik="1119190"):
        assert fila["cik"] == "1119190"


# --- Aislamiento de fallos --------------------------------------------------


class _ConexionFalsa:
    """Imita a psycopg en lo único que importa aquí: tras un error, la
    transacción queda ABORTADA y toda sentencia siguiente falla hasta que se
    hace rollback. Sin eso, el test pasaría igual con y sin el arreglo."""

    def __init__(self, ciks, cik_que_falla):
        self.ciks = ciks
        self.cik_que_falla = cik_que_falla
        self.abortada = False
        self.rollbacks = 0
        self.guardados = []

    def cursor(self):
        conexion = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, *a, **kw):
                if conexion.abortada:
                    raise RuntimeError("current transaction is aborted, commands ignored")

            def executemany(self, *a, **kw):
                self.execute()

            def fetchall(self):
                return [{"cik": c} for c in conexion.ciks]

        return _Cursor()

    def commit(self):
        if self.abortada:
            raise RuntimeError("current transaction is aborted, commands ignored")

    def rollback(self):
        self.rollbacks += 1
        self.abortada = False


def test_un_cik_roto_no_se_lleva_por_delante_a_los_demas(monkeypatch):
    """EL fallo de producción: un solo CIK con la clave ajena mal dejó la
    transacción abortada y las 134 empresas siguientes fallaron en cadena con
    'current transaction is aborted'. El contador dijo '135 fallidas' cuando el
    fallo era UNO."""
    from pipeline.ingest import xbrl_fundamentals as xf

    ciks = ["111", "222", "333", "444"]
    conn = _ConexionFalsa(ciks, cik_que_falla="222")

    monkeypatch.setattr(xf, "fetch_company_facts", lambda cik: {"cik": cik, "facts": {"us-gaap": {}}})
    monkeypatch.setattr(xf, "parse_company_facts", lambda facts, cik=None: [{"cik": cik or facts["cik"]}])

    def _store(conexion, filas):
        if filas[0]["cik"] == conn.cik_que_falla:
            conn.abortada = True
            raise RuntimeError('violates foreign key constraint "fundamentals_cik_fkey"')
        conexion.commit()
        conn.guardados.append(filas[0]["cik"])
        return len(filas)

    monkeypatch.setattr(xf, "store_fundamentals", _store)

    resultado = xf.ingest_universe_fundamentals(conn)

    assert resultado["n_failed"] == 1           # uno, no cuatro
    assert conn.guardados == ["111", "333", "444"]  # los de después SÍ se guardan
    assert conn.rollbacks == 1


def test_sin_rollback_el_fallo_se_propagaria(monkeypatch):
    """Comprobación de que el test de arriba no pasa por casualidad: si se
    quita el rollback, la cascada reaparece. Lo que se verifica es que la
    conexión falsa REPRODUCE el comportamiento de Postgres."""
    conn = _ConexionFalsa(["111"], cik_que_falla="111")
    conn.abortada = True
    with pytest.raises(RuntimeError, match="transaction is aborted"):
        conn.commit()
    conn.rollback()
    conn.commit()  # ya no debe lanzar
