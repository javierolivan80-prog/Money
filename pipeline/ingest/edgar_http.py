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
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:  # noqa: PERF203
            last_exc = exc
            logger.warning("Fallo en %s (intento %d): %s", url, attempt, exc)
    raise RuntimeError(f"No se pudo descargar {url} tras reintentos") from last_exc
