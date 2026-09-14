"""test_historical_analogues.py — Etapa 6.

Dos frentes: la estadística pura (compute_impact_estimate, sin I/O) y la
recuperación real contra Postgres (get_historical_analogues), donde lo
importante a probar es que el filtro anti-look-ahead por fecha funciona de
verdad contra SQL real, no solo en la firma de la función.
"""
import os
from dataclasses import dataclass
from datetime import date, datetime

import pytest

from pipeline.analyze.historical_analogues import (
    MIN_ANALOGUES_FOR_ANY_CONFIDENCE,
    compute_impact_estimate,
)

pytestmark_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")

# ---------------------------------------------------------------------------
# compute_impact_estimate — pura
# ---------------------------------------------------------------------------


def test_zero_analogues_returns_zero_confidence_not_a_crash():
    result = compute_impact_estimate([], [])
    assert result.n_analogues == 0
    assert result.confidence == 0.0
    assert result.expected_direction == 0.0


def test_few_analogues_shrink_toward_class_prior():
    """Con muy pocos análogos, la magnitud esperada debe acercarse al prior de
    la clase, no a la media cruda de esas pocas observaciones (que podría ser
    un outlier)."""
    # 3 análogos con una media cruda enorme (+15%), pero un prior de clase de +1%.
    result_few = compute_impact_estimate([15.0, 16.0, 14.0], [1.5, 1.5, 1.5], class_prior_mean_car_pct=1.0)
    # La magnitud shrunk debe estar MUY por debajo de la media cruda de 15%,
    # y más cerca del prior de 1% que la media pura de la muestra.
    raw_mean = 15.0
    assert abs(result_few.expected_magnitude_pct - 1.0) < abs(result_few.expected_magnitude_pct - raw_mean)


def test_many_analogues_trust_the_sample_mean_over_the_prior():
    """Con muchos análogos consistentes, el shrinkage debe pesar poco: la
    magnitud debe acercarse MUCHO MÁS a la media muestral que al prior — nunca
    llega a igualarla del todo (n/(n+K) < 1 para cualquier n finito, es el
    comportamiento correcto de un estimador tipo James-Stein, no una fuga)."""
    cars = [8.0] * 100  # 100 análogos, todos con CAR=8%, sin dispersión
    result = compute_impact_estimate(cars, [1.2] * 100, class_prior_mean_car_pct=0.0)
    assert result.expected_magnitude_pct == pytest.approx(8.0, abs=2.0)
    assert result.expected_magnitude_pct > 6.5  # muy lejos del prior (0.0), cerca de la muestra


def test_probability_thresholds_are_empirical_frequencies():
    # 4 de 10 análogos con |CAR| >= 5%, 1 de 10 con |CAR| >= 10%, 0 con >= 20%
    cars = [6.0, -7.0, 5.5, -6.5, 2.0, 1.0, -1.0, 3.0, -2.0, -11.0]
    result = compute_impact_estimate(cars, [1.0] * 10)
    assert result.probability_5pct_move == pytest.approx(50.0)
    assert result.probability_10pct_move == pytest.approx(10.0)
    assert result.probability_20pct_move == pytest.approx(0.0)


def test_expected_direction_is_zero_when_shrunk_magnitude_near_zero():
    cars = [3.0, -3.2, 2.9, -2.8]  # se cancelan, media casi 0
    result = compute_impact_estimate(cars, [1.0] * 4, class_prior_mean_car_pct=0.0)
    assert result.expected_direction == 0.0


def test_confidence_below_minimum_analogues_is_capped_low():
    result = compute_impact_estimate([5.0, 5.0], [1.0, 1.0])  # solo 2 análogos, por debajo del mínimo
    assert len([5.0, 5.0]) < MIN_ANALOGUES_FOR_ANY_CONFIDENCE
    assert result.confidence <= 15.0


def test_confidence_increases_with_sample_size_holding_dispersion_fixed():
    small = compute_impact_estimate([5.0] * 10, [1.0] * 10)
    large = compute_impact_estimate([5.0] * 60, [1.0] * 60)
    assert large.confidence > small.confidence


def test_confidence_decreases_with_dispersion_holding_n_fixed():
    tight = compute_impact_estimate([5.0, 5.1, 4.9, 5.0, 5.1] * 6, [1.0] * 30)
    wide = compute_impact_estimate([5.0, -10.0, 15.0, -8.0, 12.0] * 6, [1.0] * 30)
    assert tight.confidence > wide.confidence


def test_volatility_increase_scales_with_volume_ratio_and_is_bounded():
    normal = compute_impact_estimate([2.0] * 10, [1.0] * 10)
    high_vol = compute_impact_estimate([2.0] * 10, [3.5] * 10)
    assert 0 <= normal.volatility_increase <= 100
    assert 0 <= high_vol.volatility_increase <= 100
    assert high_vol.volatility_increase > normal.volatility_increase


def test_as_json_matches_spec_field_names():
    result = compute_impact_estimate([5.0, -3.0, 6.0], [1.2, 1.1, 1.3])
    payload = result.as_json()
    expected_keys = {
        "probability_5pct_move", "probability_10pct_move", "probability_20pct_move",
        "expected_direction", "expected_magnitude", "volatility_increase", "confidence",
    }
    assert set(payload.keys()) == expected_keys
    assert payload["expected_direction"] in (1.0, -1.0, 0.0)


# ---------------------------------------------------------------------------
# get_historical_analogues / estimate_impact_for_event — contra Postgres real
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

    def _insert_event(self, cik: str, d0: date, event_class: str = "8K_2.02_EARNINGS", car: float = 0.05) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
                "VALUES (%s, %s, 'X', %s, %s) ON CONFLICT (cik) DO NOTHING",
                (cik, f"T{cik}", d0, d0),
            )
            cur.execute(
                """
                INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                    accession_number, source_url, filed_at, d0_close_date, classification_method,
                    classification_confidence, raw_text_hash)
                VALUES (%s, %s, 'EDGAR', FALSE, %s, ARRAY['2.02'], %s, 'https://x', %s, %s, 'RULE', 1.0, %s)
                RETURNING event_id
                """,
                (cik, f"T{cik}", event_class, f"acc-{cik}-{d0}", d0, d0, f"hash-{cik}-{d0}"),
            )
            event_id = cur.fetchone()["event_id"]
            cur.execute(
                "INSERT INTO car_results (event_id, window_days, car, abnormal_volume_ratio, n_estimation_days) "
                "VALUES (%s, 20, %s, 1.5, 200)",
                (event_id, car),
            )
        self.conn.commit()
        return event_id

    def test_analogues_exclude_events_on_or_after_as_of_date(self):
        from pipeline.analyze.historical_analogues import get_historical_analogues

        past_id = self._insert_event("1", date(2023, 1, 10))
        future_id = self._insert_event("2", date(2024, 6, 1))
        target_date = date(2024, 1, 1)

        analogues = get_historical_analogues(
            self.conn, "8K_2.02_EARNINGS", as_of_date=target_date, exclude_event_id=999999, window_days=20
        )
        # Solo el evento pasado debe aparecer; el futuro (2024-06-01) queda excluido.
        assert len(analogues) == 1

    def test_analogues_exclude_the_event_itself(self):
        from pipeline.analyze.historical_analogues import get_historical_analogues

        self_id = self._insert_event("3", date(2023, 5, 1))
        analogues = get_historical_analogues(
            self.conn, "8K_2.02_EARNINGS", as_of_date=date(2024, 1, 1), exclude_event_id=self_id, window_days=20
        )
        assert len(analogues) == 0

    def test_analogues_filter_by_event_class(self):
        from pipeline.analyze.historical_analogues import get_historical_analogues

        self._insert_event("4", date(2023, 1, 1), event_class="8K_2.02_EARNINGS")
        self._insert_event("5", date(2023, 1, 2), event_class="8K_4.02_RESTATEMENT")

        analogues = get_historical_analogues(
            self.conn, "8K_2.02_EARNINGS", as_of_date=date(2024, 1, 1), exclude_event_id=999999, window_days=20
        )
        assert len(analogues) == 1

    def test_estimate_impact_for_event_end_to_end(self):
        from pipeline.analyze.historical_analogues import estimate_impact_for_event

        for i in range(10):
            self._insert_event(str(100 + i), date(2022, 1, 1 + i))

        result = estimate_impact_for_event(
            self.conn, "8K_2.02_EARNINGS", as_of_date=date(2024, 1, 1), exclude_event_id=999999, window_days=20
        )
        assert result.n_analogues == 10
        assert result.confidence > 0

    def test_class_prior_excludes_events_on_or_after_as_of_date(self):
        """Regresión: el prior de shrinkage se calculaba sobre la clase ENTERA,
        futuro incluido. Es look-ahead, y pesa más cuanto menos análogos previos
        hay — justo en las observaciones más frágiles del backtest."""
        from pipeline.analyze.historical_analogues import get_class_prior_mean

        # Pasado tranquilo (+1%) y futuro extremo (+50%). Si el futuro se cuela,
        # la media se dispara muy por encima del 1%.
        self._insert_event("200", date(2022, 3, 1), car=0.01)
        self._insert_event("201", date(2022, 3, 2), car=0.01)
        self._insert_event("202", date(2024, 9, 1), car=0.50)
        self._insert_event("203", date(2024, 9, 2), car=0.50)

        prior = get_class_prior_mean(self.conn, "8K_2.02_EARNINGS", 20, as_of_date=date(2023, 1, 1))
        assert prior == pytest.approx(1.0, abs=0.01), f"prior contaminado por eventos futuros: {prior}"

    def test_class_prior_is_zero_when_no_prior_events_exist(self):
        """Sin nada anterior a as_of_date se contrae hacia 'sin efecto' (0.0),
        no hacia la media del futuro ni hacia None."""
        from pipeline.analyze.historical_analogues import get_class_prior_mean

        self._insert_event("300", date(2025, 1, 1), car=0.42)

        prior = get_class_prior_mean(self.conn, "8K_2.02_EARNINGS", 20, as_of_date=date(2021, 1, 1))
        assert prior == 0.0

    def test_estimate_impact_is_not_contaminated_by_future_events(self):
        """El mismo look-ahead, visto de extremo a extremo: con pocos análogos
        previos el shrinkage da casi todo el peso al prior, así que un prior
        contaminado arrastra la magnitud esperada — y con ella el EV."""
        from pipeline.analyze.historical_analogues import estimate_impact_for_event

        self._insert_event("400", date(2022, 6, 1), car=0.01)
        self._insert_event("401", date(2022, 6, 2), car=0.01)
        for i in range(20):
            self._insert_event(str(500 + i), date(2024, 6, 1), car=0.50)

        result = estimate_impact_for_event(
            self.conn, "8K_2.02_EARNINGS", as_of_date=date(2023, 1, 1), exclude_event_id=999999, window_days=20
        )
        assert result.n_analogues == 2
        # 2 análogos al +1% con prior +1% => magnitud ~1%, lejos del +50% futuro.
        assert result.expected_magnitude_pct == pytest.approx(1.0, abs=0.2)
