"""test_filing_text.py — Fase 3, extracción de texto de filings.

No se pudo verificar el parser contra un filing real (egress bloqueado a
www.sec.gov). El fixture sigue el formato documentado y estable de EDGAR
("full submission text file": bloques <DOCUMENT> con TYPE/SEQUENCE/FILENAME/
TEXT) — ver la advertencia en el docstring de filing_text.py sobre qué
verificar a mano antes de un backfill completo.
"""
import os
from pathlib import Path

import pytest

from pipeline.ingest.filing_text import (
    MAX_TEXT_CHARS,
    extract_best_text,
    parse_submission_documents,
    strip_html_to_text,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_docs():
    raw = (FIXTURES / "sample_8k_multidoc.txt").read_text()
    return parse_submission_documents(raw)


# ---------------------------------------------------------------------------
# parse_submission_documents
# ---------------------------------------------------------------------------


def test_parse_submission_documents_finds_all_three_blocks():
    docs = _load_docs()
    assert len(docs) == 3
    assert [d["type"] for d in docs] == ["8-K", "EX-99.1", "EX-101.SCH"]


def test_parse_submission_documents_extracts_sequence_and_filename():
    docs = _load_docs()
    primary = docs[0]
    assert primary["sequence"] == 1
    assert primary["filename"] == "form8k.htm"


def test_parse_submission_documents_extracts_raw_text_block():
    docs = _load_docs()
    exhibit = docs[1]
    assert "Record Quarterly Revenue" in exhibit["raw_text"]


def test_parse_submission_documents_handles_empty_input_gracefully():
    assert parse_submission_documents("") == []
    assert parse_submission_documents("no documents here") == []


# ---------------------------------------------------------------------------
# strip_html_to_text
# ---------------------------------------------------------------------------


def test_strip_html_to_text_removes_tags_and_keeps_content():
    html = "<html><body><p>Hello <b>World</b></p></body></html>"
    assert strip_html_to_text(html) == "Hello World"


def test_strip_html_to_text_removes_script_and_style_blocks_entirely():
    docs = _load_docs()
    exhibit_text = docs[1]["raw_text"]
    cleaned = strip_html_to_text(exhibit_text)
    assert "trackingPixel" not in cleaned
    assert "display: none" not in cleaned
    assert "Record Quarterly Revenue" in cleaned


def test_strip_html_to_text_collapses_whitespace():
    html = "<p>Line one</p>\n\n\n<p>   Line   two   </p>"
    result = strip_html_to_text(html)
    assert "  " not in result  # sin dobles espacios
    assert "Line one" in result and "Line two" in result


# ---------------------------------------------------------------------------
# extract_best_text
# ---------------------------------------------------------------------------


def test_extract_best_text_prefers_exhibit_for_earnings():
    docs = _load_docs()
    result = extract_best_text(docs, prefer_exhibit=True)
    assert result["includes_exhibit"] is True
    assert "Record Quarterly Revenue" in result["text"]
    assert "412 million" in result["text"]


def test_extract_best_text_excludes_xbrl_exhibit():
    """El exhibit XBRL (EX-101.SCH) no debe colarse — solo EX-99* cuenta como
    'exhibit de prensa' (ver _PRESS_RELEASE_EXHIBIT_PREFIX)."""
    docs = _load_docs()
    result = extract_best_text(docs, prefer_exhibit=True)
    assert "irrelevant structured data" not in result["text"]


def test_extract_best_text_without_prefer_exhibit_uses_only_primary():
    docs = _load_docs()
    result = extract_best_text(docs, prefer_exhibit=False)
    assert result["includes_exhibit"] is False
    assert "Record Quarterly Revenue" not in result["text"]
    assert "Results of Operations" in result["text"]


def test_extract_best_text_empty_documents_list():
    result = extract_best_text([])
    assert result == {"text": "", "includes_exhibit": False, "length_chars": 0}


def test_extract_best_text_truncates_to_max_chars():
    huge_doc = [{"type": "8-K", "sequence": 1, "filename": "x.htm", "raw_text": "A" * (MAX_TEXT_CHARS * 3)}]
    result = extract_best_text(huge_doc, prefer_exhibit=False)
    assert result["length_chars"] <= MAX_TEXT_CHARS


def test_extract_best_text_falls_back_to_first_when_no_sequence_present():
    """Documentos sin <SEQUENCE> (edge case tolerado, ver parse_submission_documents)
    no deben hacer que extract_best_text reviente."""
    docs = [
        {"type": "8-K", "sequence": None, "filename": "a.htm", "raw_text": "<p>Primary body</p>"},
        {"type": "EX-99.1", "sequence": None, "filename": "b.htm", "raw_text": "<p>Press release</p>"},
    ]
    result = extract_best_text(docs, prefer_exhibit=True)
    assert "Press release" in result["text"]
    assert "Primary body" in result["text"]


# ---------------------------------------------------------------------------
# populate_missing_filing_text — contra Postgres real, con red simulada
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
class TestPopulateAgainstRealPostgres:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        from pipeline.db.connection import get_connection, init_schema

        self.conn = get_connection()
        init_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(
                "TRUNCATE car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
                "fama_french_factors, universe RESTART IDENTITY CASCADE"
            )
        self.conn.commit()

        # Simula la respuesta HTTP de EDGAR con el fixture — no hay red real
        # hacia www.sec.gov desde este sandbox (ver docstring del módulo).
        fixture_text = (FIXTURES / "sample_8k_multidoc.txt").read_text()

        class _FakeResponse:
            text = fixture_text

        monkeypatch.setattr("pipeline.ingest.filing_text.throttled_get", lambda url, **kw: _FakeResponse())

        yield
        self.conn.close()

    def _seed_event(self, event_class="8K_2.02_EARNINGS", source="EDGAR"):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES ('1','ACME','Acme Widgets Corp','2024-01-01','2024-01-01')"
            )
            cur.execute(
                """
                INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES ('1','ACME',%s,FALSE,%s,ARRAY['2.02'],'acc1','https://www.sec.gov/x','2024-03-15','2024-03-15','RULE',1.0,'h1')
                RETURNING event_id
                """,
                (source, event_class),
            )
            event_id = cur.fetchone()["event_id"]
        self.conn.commit()
        return event_id

    def test_populate_fills_filing_text_for_edgar_event(self):
        from pipeline.ingest.filing_text import populate_missing_filing_text

        event_id = self._seed_event()
        n = populate_missing_filing_text(self.conn)
        assert n == 1

        with self.conn.cursor() as cur:
            cur.execute("SELECT filing_text, filing_text_includes_exhibit FROM events WHERE event_id = %s", (event_id,))
            row = cur.fetchone()
        assert "Record Quarterly Revenue" in row["filing_text"]
        assert row["filing_text_includes_exhibit"] is True

    def test_populate_is_idempotent(self):
        from pipeline.ingest.filing_text import populate_missing_filing_text

        self._seed_event()
        first = populate_missing_filing_text(self.conn)
        second = populate_missing_filing_text(self.conn)
        assert first == 1
        assert second == 0  # ya no quedan eventos EDGAR con filing_text NULL

    def test_populate_skips_fda_events(self):
        from pipeline.ingest.filing_text import populate_missing_filing_text

        self._seed_event(event_class="FDA_CRL", source="FDA_OPENFDA")
        n = populate_missing_filing_text(self.conn)
        assert n == 0  # no hay scraper de FDA todavía — se salta explícitamente
