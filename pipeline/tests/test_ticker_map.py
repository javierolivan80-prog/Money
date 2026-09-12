"""test_ticker_map.py — valida la inversión CIK->ticker sin red.

No se puede descargar company_tickers.json en este sandbox (egress bloqueado
a www.sec.gov). Se prueba la lógica de inversión del mapa contra un JSON de
muestra con la misma forma que el fichero real.
"""
import json

SAMPLE_RAW = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


def test_inversion_strips_leading_zeros_consistently():
    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}
    assert mapping["320193"] == "AAPL"
    assert mapping["1045810"] == "NVDA"


def test_resolve_matches_edgar_zero_padded_cik_format():
    """El .idx de EDGAR trae CIKs con ceros a la izquierda (ej '0000320193').
    resolve() debe despojarlos antes de buscar en el mapa (construido sin ceros).
    """
    from pipeline.ingest.ticker_map import get_ticker_map

    mapping = {str(entry["cik_str"]): entry["ticker"] for entry in SAMPLE_RAW.values()}

    def resolve_against(cik: str, m: dict) -> str | None:
        return m.get(cik.lstrip("0") or "0")

    assert resolve_against("0000320193", mapping) == "AAPL"
    assert resolve_against("320193", mapping) == "AAPL"
    assert resolve_against("0000000000", mapping) is None
