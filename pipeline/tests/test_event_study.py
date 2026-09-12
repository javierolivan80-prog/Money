"""test_event_study.py — Fase 6 PARTE 1. compute_mde y
compute_event_study_for_class son puros (cálculo a mano); run_event_study
necesita Postgres real (lee car_results)."""
import os

import numpy as np
import pytest
from scipy import stats

from pipeline.validation.event_study import (
    MDE_POWER_CONSTANT,
    SIGNIFICANCE_ALPHA,
    compute_event_study_for_class,
    compute_mde,
)

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


# ---------------------------------------------------------------------------
# compute_mde — puro, fórmula literal de AUDIT_LEAN.md §2.2.3
# ---------------------------------------------------------------------------


def test_compute_mde_hand_calculated():
    # MDE = 2.8 * sigma / sqrt(n)
    mde = compute_mde(sigma=8.0, n=100)
    assert mde == pytest.approx(2.8 * 8.0 / 10)  # sqrt(100)=10 -> 2.24


def test_compute_mde_matches_audit_lean_example():
    # AUDIT_LEAN.md §2.2.3: earnings, n grande, sigma ~8% -> MDE de decenas de bps
    mde = compute_mde(sigma=8.0, n=10_000)
    assert mde == pytest.approx(2.8 * 8.0 / 100)  # sqrt(10000)=100 -> 0.224 puntos = ~22 bps


def test_compute_mde_none_for_zero_n():
    assert compute_mde(sigma=8.0, n=0) is None


# ---------------------------------------------------------------------------
# compute_event_study_for_class — puro
# ---------------------------------------------------------------------------


def test_event_study_hand_calculated_stats():
    car_values = [0.01, 0.02, 0.03, 0.04, 0.05]  # fracciones -> 1,2,3,4,5 en %
    result = compute_event_study_for_class(car_values)

    assert result["n"] == 5
    assert result["mean_return_pct"] == pytest.approx(3.0)
    assert result["median_return_pct"] == pytest.approx(3.0)
    expected_sigma = float(np.array([1, 2, 3, 4, 5]).std(ddof=1))
    assert result["sigma_pct"] == pytest.approx(expected_sigma)
    assert result["mde_pct"] == pytest.approx(MDE_POWER_CONSTANT * expected_sigma / np.sqrt(5))

    expected_t, expected_p = stats.ttest_1samp(np.array([1, 2, 3, 4, 5]), popmean=0.0)
    assert result["t_statistic"] == pytest.approx(float(expected_t))
    assert result["p_value"] == pytest.approx(float(expected_p))
    assert result["significant"] == (float(expected_p) < SIGNIFICANCE_ALPHA)


def test_event_study_insufficient_sample_returns_none_stats():
    result = compute_event_study_for_class([0.01, 0.02])  # n=2 < MIN_N_FOR_ANY_STATISTIC
    assert result["n"] == 2
    assert result["mean_return_pct"] is None
    assert result["significant"] is None
    assert "insuficiente" in result["conclusion"]


def test_event_study_zero_mean_is_not_significant():
    # Retornos simétricos alrededor de 0 -> media ~0 -> no significativo.
    car_values = [0.01, -0.01, 0.02, -0.02, 0.005, -0.005]
    result = compute_event_study_for_class(car_values)
    assert result["significant"] is False
    assert "No significativo" in result["conclusion"]


def test_event_study_large_consistent_effect_is_significant():
    car_values = [0.05, 0.06, 0.055, 0.052, 0.058, 0.061, 0.049, 0.053]  # todos claramente positivos, poca varianza
    result = compute_event_study_for_class(car_values)
    assert result["significant"] is True
    assert "Significativo" in result["conclusion"]


# ---------------------------------------------------------------------------
# run_event_study — integración contra Postgres real
# ---------------------------------------------------------------------------


@pytestmark_db
class TestEventStudyIntegration:
    @pytest.fixture
    def conn(self):
        from pipeline.db.connection import get_connection, init_schema

        c = get_connection()
        init_schema(c)
        with c.cursor() as cur:
            cur.execute("TRUNCATE car_results, events, universe RESTART IDENTITY CASCADE")
        c.commit()
        yield c
        c.close()

    def _seed_event_with_car(self, conn, cik, ticker, event_class, car_value, window_days=20):
        from datetime import date

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
                (cik, ticker, date(2024, 1, 1), date(2024, 1, 1)),
            )
            cur.execute(
                """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['2.02'],%s,'https://x','2024-01-02','2024-01-02','RULE',1.0,%s)
                RETURNING event_id""",
                (cik, ticker, event_class, f"acc-{cik}", f"hash-{cik}"),
            )
            event_id = cur.fetchone()["event_id"]
            cur.execute(
                "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, %s, %s, 100)",
                (event_id, window_days, car_value),
            )
        conn.commit()

    def test_run_event_study_groups_by_class(self, conn):
        from pipeline.validation.event_study import run_event_study

        for i in range(5):
            self._seed_event_with_car(conn, f"e{i}", f"E{i}", "8K_2.02_EARNINGS", 0.02 + i * 0.001)
        for i in range(3):
            self._seed_event_with_car(conn, f"m{i}", f"M{i}", "8K_1.01_MATERIAL_AGREEMENT", -0.01)

        result = run_event_study(conn, window_days=20)
        assert set(result.keys()) == {"8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT"}
        assert result["8K_2.02_EARNINGS"]["n"] == 5
        assert result["8K_1.01_MATERIAL_AGREEMENT"]["n"] == 3

    def test_run_event_study_respects_window_days_filter(self, conn):
        from pipeline.validation.event_study import run_event_study

        self._seed_event_with_car(conn, "w1", "W1", "8K_2.02_EARNINGS", 0.05, window_days=5)
        self._seed_event_with_car(conn, "w2", "W2", "8K_2.02_EARNINGS", 0.03, window_days=20)

        result_20 = run_event_study(conn, window_days=20)
        assert result_20["8K_2.02_EARNINGS"]["n"] == 1
