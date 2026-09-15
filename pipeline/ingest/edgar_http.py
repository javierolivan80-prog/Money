"""edgar_http.py — GET rate-limitado y con reintentos hacia EDGAR, compartido.

Extraído de edgar_scraper.py en la Fase 3 porque filing_text.py necesita
exactamente el mismo comportamiento (User-Agent con contacto real, límite de
10 req/s de la SEC, backoff en 429/fallos transitorios) para descargar el
cuerpo completo de cada filing. Duplicarlo en dos sitios es precisamente el
tipo de cosa que diverge en silencio con el tiempo (ver el mismo razonamiento
en backtest/factor_model.py, extraído en la Fase 2 por el mismo motivo).
"""
from __future__ import annotations

import logging
import time

import requests

from pipeline import config

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": config.EDGAR_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
_RATE_LIMIT_DELAY = 1.0 / config.EDGAR_RATE_LIMIT_PER_SEC


class PermanentHTTPError(RuntimeError):
    """4xx que no tiene sentido reintentar (404, 403...).

    Se separa de los fallos transitorios porque reintentar un error permanente
    no solo no arregla nada: lo ESCONDE. Con una URL mal construida, cada
    filing costaba 30 segundos de esperas (2+4+8+16) antes de rendirse, así que
    un fallo que debería saltar a la vista en un segundo convertía la ingesta de
    un día en un proceso de más de media hora que parecía estar trabajando.
    """


def _es_permanente(status: int) -> bool:
    # 429 (rate limit) es 4xx pero SÍ es transitorio: es justo lo que hay que
    # reintentar. 408 (timeout) igual.
    return 400 <= status < 500 and status not in (408, 429)


def throttled_get(url: str, **kwargs) -> requests.Response:
    """GET con rate limit fijo y reintentos con backoff exponencial.

    La SEC devuelve 429 si se supera el límite; también hay que tolerar caídas
    de red transitorias. Backoff: 2s, 4s, 8s, 16s (misma política que el resto
    del proyecto, por consistencia).
    """
    delays = [2, 4, 8, 16]
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        time.sleep(_RATE_LIMIT_DELAY)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
            if resp.status_code == 429:
                logger.warning("429 de EDGAR en %s, reintentando", url)
                continue
            if _es_permanente(resp.status_code):
                raise PermanentHTTPError(f"{resp.status_code} en {url} — no se reintenta")
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning("Fallo en %s (intento %d): %s", url, attempt, exc)
    raise RuntimeError(f"No se pudo descargar {url} tras reintentos") from last_exc


# Corte de seguridad para la descarga parcial. La cabecera SGML de un
# submission son 1-3 KB; 256 KB deja margen de sobra para cabeceras raras y
# aun así evita traerse el cuerpo entero.
_HEADER_BYTE_CAP = 256 * 1024
_HEADER_END_MARKER = "</SEC-HEADER>"


def throttled_get_header(url: str) -> str:
    """Igual que throttled_get, pero corta la descarga en cuanto termina la
    cabecera SGML del submission.

    MOTIVO (medido, no supuesto): el .txt de un submission completo incluye
    TODOS los documentos y anexos del filing — habitualmente varios MB, a veces
    decenas. De todo eso, la ingesta solo lee las líneas 'ACCESSION NUMBER:' e
    'ITEM INFORMATION:', que están en los primeros KB. Descargarlo entero para
    leer la cabecera hacía que un solo día de backfill (unos cientos de 8-K)
    tardara más de media hora, casi toda en transferencia tirada a la basura.

    El corte es por contenido (`</SEC-HEADER>`) con tope duro por bytes: si un
    fichero no trae el marcador, se devuelven los primeros 256 KB y el parseo
    sigue como antes en vez de fallar. Nunca devuelve MENOS de lo que un
    parser de cabecera necesita.
    """
    delays = [2, 4, 8, 16]
    last_exc: Exception | None = None
    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        time.sleep(_RATE_LIMIT_DELAY)
        try:
            with requests.get(url, headers=HEADERS, timeout=30, stream=True) as resp:
                if resp.status_code == 429:
                    logger.warning("429 de EDGAR en %s, reintentando", url)
                    continue
                if _es_permanente(resp.status_code):
                    raise PermanentHTTPError(f"{resp.status_code} en {url} — no se reintenta")
                resp.raise_for_status()
                chunks: list[str] = []
                total = 0
                # Ventana con la cola del trozo anterior: el marcador puede
                # quedar partido entre trozos (y con trozos pequeños, entre
                # más de dos), así que buscarlo solo en el último se lo salta
                # y la descarga seguiría hasta el tope de bytes.
                solapamiento = len(_HEADER_END_MARKER) - 1
                cola = ""
                for chunk in resp.iter_content(chunk_size=8192, decode_unicode=True):
                    if not chunk:
                        continue
                    if isinstance(chunk, bytes):  # decode_unicode no aplica sin charset
                        chunk = chunk.decode("utf-8", errors="replace")
                    chunks.append(chunk)
                    total += len(chunk)
                    if _HEADER_END_MARKER in cola + chunk or total >= _HEADER_BYTE_CAP:
                        break
                    cola = (cola + chunk)[-solapamiento:]
                return "".join(chunks)
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning("Fallo en %s (intento %d): %s", url, attempt, exc)
    raise RuntimeError(f"No se pudo descargar la cabecera de {url} tras reintentos") from last_exc
