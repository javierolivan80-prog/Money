"""test_guidance_detector.py — Fase 3, Stage 2 support."""
import os
from datetime import date

import pytest

from pipeline.analyze.guidance_detector import detect_guidance, detect_rumor

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")

# ---------------------------------------------------------------------------
# detect_guidance / detect_rumor — puras
# ---------------------------------------------------------------------------


def test_detect_guidance_true_on_keyword_match():
    assert detect_guidance(["We are raising our full-year outlook to $2B."]) is True


def test_detect_guidance_false_when_no_keywords():
    assert detect_guidance(["The board appointed a new director."]) is False


def test_detect_guidance_false_on_empty_list():
    assert detect_guidance([]) is False


def test_detect_guidance_ignores_none_entries_without_crashing():
    # OJO: no usar una frase que contenga la palabra "guidance" al negarla
    # ("no guidance here" SÍ dispara el keyword — falso positivo esperado y
    # documentado del heurístico, no un caso a probar aquí).
    assert detect_guidance([None, "The board appointed a new director."]) is False


def test_detect_guidance_case_insensitive():
    assert detect_guidance(["WE EXPECT strong demand next quarter"]) is True


def test_detect_rumor_true_on_keyword_match():
    assert detect_rumor(["Sources familiar with the matter say a deal is imminent."]) is True


def test_detect_rumor_false_when_no_keywords():
    assert detect_rumor(["Quarterly revenue was $100 million."]) is False


def test_detect_guidance_and_rumor_are_independent():
    text = "We are raising our outlook. Sources familiar with the matter agree."
    assert detect_guidance([text]) is True
    assert detect_rumor([text]) is True

    only_guidance = "We are reaffirming our full-year outlook."
    assert detect_guidance([only_guidance]) is True
    assert detect_rumor([only_guidance]) is False


# ---------------------------------------------------------------------------
# get_prior_filing_texts / compute_novelty_signals — contra Postgres real
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")
class TestAgainstRealPostgres:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from pipeline.db.connection import get_connection, init_schema

        self.conn = get_connection()
        init_schema(self.conn)
        with self.conn.cursor() as cur:
            cur.execute(
                "TRUNCATE car_results, backtest_runs, event_analyses, event_enrichment, events, prices, "
                "fama_french_factors, universe RESTART IDENTITY CASCADE"
            )
        self.conn.commit()
        yield
        self.conn.close()

    def _seed_event_with_text(self, cik: str, ticker: str, d0: date, filing_text: str | None):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO NOTHING",
                (cik, ticker, d0, d0),
            )
            cur.execute(
                """
                INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash, filing_text)
                VALUES (%s,%s,'EDGAR',FALSE,'8K_2.02_EARNINGS',ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s,%s)
                """,
                (cik, ticker, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}", filing_text),
            )
        self.conn.commit()

    def test_prior_texts_exclude_events_on_or_after_before_date(self):
        from pipeline.analyze.guidance_detector import get_prior_filing_texts

        self._seed_event_with_text("1", "ACME", date(2024, 1, 1), "past filing text")
        self._seed_event_with_text("1", "ACME", date(2024, 6, 1), "future filing text")

        texts = get_prior_filing_texts(self.conn, "ACME", before_date=date(2024, 3, 1), lookback_days=180)
        assert texts == ["past filing text"]

    def test_prior_texts_respect_lookback_window(self):
        from pipeline.analyze.guidance_detector import get_prior_filing_texts

        self._seed_event_with_text("1", "ACME", date(2023, 1, 1), "too old")  # fuera de la ventana de 30 días
        self._seed_event_with_text("1", "ACME", date(2024, 2, 20), "recent")

        texts = get_prior_filing_texts(self.conn, "ACME", before_date=date(2024, 3, 1), lookback_days=30)
        assert texts == ["recent"]

    def test_prior_texts_skip_events_without_extracted_text(self):
        from pipeline.analyze.guidance_detector import get_prior_filing_texts

        self._seed_event_with_text("1", "ACME", date(2024, 1, 1), None)  # sin texto extraído todavía
        texts = get_prior_filing_texts(self.conn, "ACME", before_date=date(2024, 3, 1), lookback_days=180)
        assert texts == []

    def test_compute_novelty_signals_returns_none_when_no_prior_texts_at_all(self):
        from pipeline.analyze.guidance_detector import compute_novelty_signals

        # Ningún evento previo sembrado -> ambas señales deben ser None (no False).
        guidance, rumor = compute_novelty_signals(self.conn, "NEWCO", date(2024, 3, 1))
        assert guidance is None
        assert rumor is None

    def test_compute_novelty_signals_detects_guidance_from_prior_earnings(self):
        from pipeline.analyze.guidance_detector import compute_novelty_signals

        # A 15 días de as_of_date: dentro de AMBAS ventanas (guidance 180d, rumor 30d).
        self._seed_event_with_text("1", "ACME", date(2024, 2, 15), "We are raising our full-year outlook to $2B.")
        guidance, rumor = compute_novelty_signals(self.conn, "ACME", date(2024, 3, 1))
        assert guidance is True
        # Hay texto en la ventana de rumor, pero no contiene keywords de rumor -> False, no None.
        assert rumor is False

    def test_compute_novelty_signals_rumor_is_none_when_outside_its_shorter_window(self):
        """Un filing de guidance de hace 45 días cae dentro de la ventana de
        guidance (180d) pero FUERA de la de rumor (30d) — rumor debe ser None
        (sin dato), no False (confirmado ausente)."""
        from pipeline.analyze.guidance_detector import compute_novelty_signals

        self._seed_event_with_text("1", "ACME", date(2024, 1, 15), "We are raising our full-year outlook to $2B.")
        guidance, rumor = compute_novelty_signals(self.conn, "ACME", date(2024, 3, 1))
        assert guidance is True
        assert rumor is None
