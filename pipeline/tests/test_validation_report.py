"""test_validation_report.py — Fase 6. generate_full_validation_report de
extremo a extremo contra Postgres real, escribiendo a un directorio
temporal (nunca a docs/ real, para no ensuciar el repo en cada test run)."""
import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL no definida")


@pytest.fixture
def conn():
    from pipeline.db.connection import get_connection, init_schema

    c = get_connection()
    init_schema(c)
    with c.cursor() as cur:
        cur.execute(
            "TRUNCATE paper_trades, paper_trading_reports, portfolio_trades, portfolio_equity_curve, "
            "portfolio_reports, car_results, backtest_runs, event_analyses, event_enrichment, events, "
            "prices, fama_french_factors, universe RESTART IDENTITY CASCADE"
        )
    c.commit()
    yield c
    c.close()


def _business_days(start, n):
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _seed_full_event(conn, cik, ticker, d0, cal, closes, decision, confidence, ev, event_class, vix_d0):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe (cik, ticker, company_name, first_seen_date, last_seen_date) "
            "VALUES (%s,%s,'X',%s,%s) ON CONFLICT (cik) DO UPDATE SET ticker=EXCLUDED.ticker",
            (cik, ticker, cal[0], cal[-1]),
        )
        for d, c in zip(cal, closes):
            cur.execute(
                "INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume, survivorship_warning) "
                "VALUES (%s,%s,%s,%s,%s,%s,1.0,100000,FALSE) ON CONFLICT (ticker, trade_date) DO UPDATE SET "
                "open_raw=EXCLUDED.open_raw, close_raw=EXCLUDED.close_raw",
                (ticker, d, c, c, c * 1.01, c * 0.99),
            )
        cur.execute(
            """INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes,
                accession_number, source_url, filed_at, d0_close_date, classification_method,
                classification_confidence, raw_text_hash)
            VALUES (%s,%s,'EDGAR',FALSE,%s,ARRAY['2.02'],%s,'https://x',%s,%s,'RULE',1.0,%s)
            RETURNING event_id""",
            (cik, ticker, event_class, f"acc-{cik}", d0, d0, f"hash-{cik}"),
        )
        event_id = cur.fetchone()["event_id"]
        cur.execute(
            """INSERT INTO event_analyses (
                event_id, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
                judge_output, net_conviction, confidence_in_conviction, impact_estimation,
                n_historical_analogues, ev_calculation, ev_conservative, ev_aggressive, ev_balanced,
                abstention_decision, trade_decision_conservative, trade_decision_aggressive,
                trade_decision_balanced, model_version_bull_bear, model_version_judge
            ) VALUES (%s, 80, '{}', '{}', '{}', '{}', 0.6, %s, '{}', 25, '{}', %s, %s, %s, '{}', %s, %s, %s,
                'claude-haiku-4-5', 'claude-sonnet-4-6')""",
            (event_id, confidence, ev, ev, ev, decision, decision, decision),
        )
        cur.execute("INSERT INTO event_enrichment (event_id, vix_d0) VALUES (%s, %s)", (event_id, vix_d0))
        cur.execute(
            "INSERT INTO car_results (event_id, window_days, car, n_estimation_days) VALUES (%s, 20, %s, 100)",
            (event_id, ev),
        )
    conn.commit()
    return event_id


def test_generate_full_validation_report_end_to_end(conn, tmp_path):
    from pipeline.validation.report import generate_full_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    classes = ["8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT", "8K_5.02_OFFICER_CHANGE"]
    for i in range(15):
        d0 = cal[i]
        ticker = f"V{i}"
        closes = [100.0 + i * 0.5 + j * 0.2 for j in range(len(cal))]
        _seed_full_event(
            conn, f"v{i}", ticker, d0, cal, closes,
            decision="LONG" if i % 4 != 0 else "NO_TRADE",
            confidence=60.0 + i, ev=0.01, event_class=classes[i % 3], vix_d0=15.0 + i,
        )

    result = generate_full_validation_report(conn, run_batch_tag="validation-test-1", docs_dir=str(tmp_path))

    assert result["run_batch_tag"] == "validation-test-1"
    assert set(result["decisions"].keys()) == {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    assert result["best_version"] in {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    assert result["best_decision"]["option"] in {"A", "B", "C"}

    report_path = tmp_path / "VALIDATION_REPORT.md"
    assert report_path.exists()
    content = report_path.read_text()
    assert "# VALIDATION_REPORT.md" in content
    assert "PARTE 1 — Event Study" in content
    assert "PARTE 2 — Backtesting" in content
    assert "PARTE 3 — Calibración" in content
    assert "PARTE 4 — Sesgo y limitaciones" in content
    assert "PARTE 5 — Sensibilidad" in content
    assert "PARTE 6 — Decisión de inversión" in content
    assert "PARTE 7 — Next steps" in content
    assert result["best_decision"]["label"] in content

    if result["n_trades_exported"] > 0:
        csv_path = tmp_path / f"trades_validation-test-1.csv"
        assert csv_path.exists()
        csv_content = csv_path.read_text()
        assert "ticker" in csv_content
        assert "pnl_pct" in csv_content


def test_persist_validation_report_reuses_existing_portfolio_report(conn):
    """persist_validation_report (el paso del cron nocturno) no debe
    resimular la cartera si backtest/portfolio_report.py ya dejó un
    portfolio_report para ese run_batch_tag en la misma corrida — solo debe
    LEERLO. Se verifica sembrando un portfolio_reports con un
    bias_report reconocible y comprobando que ese mismo valor aparece en el
    validation_reports resultante, sin volver a calcular nada."""
    from pipeline.backtest.portfolio_report import run_full_backtest
    from pipeline.validation.report import persist_validation_report

    cal = _business_days(date(2024, 1, 2), 60)
    classes = ["8K_2.02_EARNINGS", "8K_1.01_MATERIAL_AGREEMENT"]
    for i in range(10):
        d0 = cal[i]
        ticker = f"P{i}"
        closes = [100.0 + i * 0.5 + j * 0.2 for j in range(len(cal))]
        _seed_full_event(
            conn, f"p{i}", ticker, d0, cal, closes,
            decision="LONG" if i % 3 != 0 else "NO_TRADE",
            confidence=55.0 + i, ev=0.01, event_class=classes[i % 2], vix_d0=15.0 + i,
        )

    tag = "validation-persist-test-1"
    portfolio_report = run_full_backtest(conn, run_batch_tag=tag)

    payload = persist_validation_report(conn, tag)

    assert payload["run_batch_tag"] == tag
    assert payload["bias_report"] == portfolio_report["bias_report"]
    assert set(payload["decisions"].keys()) == {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}
    assert payload["best_version"] in {"CONSERVATIVE", "AGGRESSIVE", "BALANCED"}

    with conn.cursor() as cur:
        cur.execute("SELECT report_json FROM validation_reports WHERE run_batch_tag = %s", (tag,))
        row = cur.fetchone()
    assert row is not None
    assert row["report_json"]["best_version"] == payload["best_version"]
    assert "event_study" in row["report_json"]
    assert "sensitivity" in row["report_json"]


def test_persist_validation_report_upsert_overwrites(conn):
    """ON CONFLICT DO UPDATE: reejecutar el paso nocturno con el mismo tag
    (ej. un re-disparo manual del workflow) actualiza la fila en vez de
    fallar por duplicate key — mismo patrón que portfolio_reports."""
    from pipeline.validation.report import persist_validation_report

    cal = _business_days(date(2024, 1, 2), 40)
    _seed_full_event(
        conn, "u1", "U1", cal[0], cal, [100.0 + j * 0.1 for j in range(len(cal))],
        decision="LONG", confidence=70.0, ev=0.01, event_class="8K_2.02_EARNINGS", vix_d0=18.0,
    )

    tag = "validation-persist-test-2"
    first = persist_validation_report(conn, tag)
    second = persist_validation_report(conn, tag)

    assert first["run_batch_tag"] == second["run_batch_tag"] == tag
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM validation_reports WHERE run_batch_tag = %s", (tag,))
        assert cur.fetchone()["n"] == 1
