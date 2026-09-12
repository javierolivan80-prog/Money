"""fama_french.py — descarga los factores diarios Fama-French 3 (Ken French Data Library).

Por qué esto no es opcional (AUDIT_LEAN.md §2.3): sin factores, el CAR se
calcula contra el mercado a secas y se confunde beta con alpha. Es la pieza
gratuita de mayor apalancamiento de rigor de todo el proyecto.

Es "scraping minimalista" (como dice el spec), no una API: se descarga el ZIP
público de la Ken French Data Library y se parsea el CSV que contiene.

ADVERTENCIA DE VALIDACIÓN — sin ejecutar en vivo. mba.tuck.dartmouth.edu está
bloqueado por la política de egress de este sandbox (AUDIT_LEAN.md §1.5,
confirmado). La URL y el formato de fichero de abajo son el formato
públicamente documentado y estable desde hace años, pero no se ha podido
descargar ni parsear un fichero real aquí. Verificar con una descarga manual
de una fila conocida (ej. el primer día de 2021) antes de confiar en el rango
completo.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# Factores diarios FF3 (Mkt-RF, SMB, HML, RF), en formato ZIP con un CSV dentro.
FF3_DAILY_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)


def fetch_ff3_daily() -> pd.DataFrame:
    """Descarga y parsea los factores diarios FF3.

    Formato del CSV dentro del ZIP (estable desde hace años, documentado
    públicamente): unas líneas de cabecera de texto libre, luego una tabla con
    columnas `Date,Mkt-RF,SMB,HML,RF` donde Date es YYYYMMDD y los valores
    están en puntos porcentuales (hay que dividir entre 100 para usarlos como
    retornos fraccionarios).
    """
    resp = requests.get(FF3_DAILY_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        raw = zf.read(csv_name).decode("utf-8", errors="replace")

    return parse_ff3_csv(raw)


def parse_ff3_csv(raw: str) -> pd.DataFrame:
    """Separado de fetch_ff3_daily() para poder probarse sin red, contra un
    fixture de texto con la misma forma que el CSV real."""
    lines = raw.splitlines()
    # La primera línea con 5 campos separados por coma y el primer campo de 8
    # dígitos (una fecha YYYYMMDD) marca el inicio de la tabla de datos.
    data_start = None
    for i, line in enumerate(lines):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5 and parts[0].isdigit() and len(parts[0]) == 8:
            data_start = i
            break
    if data_start is None:
        raise ValueError("No se encontró el inicio de la tabla de datos en el CSV de Ken French")

    rows = []
    for line in lines[data_start:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5 or not parts[0].isdigit() or len(parts[0]) != 8:
            break  # fin de la tabla (footer de texto libre)
        d = date(int(parts[0][:4]), int(parts[0][4:6]), int(parts[0][6:8]))
        rows.append(
            {
                "trade_date": d,
                "mkt_rf": float(parts[1]) / 100.0,
                "smb": float(parts[2]) / 100.0,
                "hml": float(parts[3]) / 100.0,
                "rf": float(parts[4]) / 100.0,
            }
        )
    return pd.DataFrame(rows)


def store_factors(conn, df: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        for _, row in df.iterrows():
            cur.execute(
                """
                INSERT INTO fama_french_factors (trade_date, mkt_rf, smb, hml, rf)
                VALUES (%(trade_date)s, %(mkt_rf)s, %(smb)s, %(hml)s, %(rf)s)
                ON CONFLICT (trade_date) DO UPDATE SET
                    mkt_rf = EXCLUDED.mkt_rf, smb = EXCLUDED.smb,
                    hml = EXCLUDED.hml, rf = EXCLUDED.rf
                """,
                row.to_dict(),
            )
    conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from pipeline.db.connection import get_connection

    df = fetch_ff3_daily()
    logger.info("Descargados %d días de factores FF3", len(df))
    store_factors(get_connection(), df)
