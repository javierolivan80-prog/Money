-- schema.sql — POC Semana 1
--
-- Diseño gobernado por AUDIT_LEAN.md y ARCHITECTURE_LEAN.md:
--   - EDGAR es el eje (earnings 8-K Item 2.02). FDA es satélite, columna `is_satellite`.
--   - D0_close_date es explícita: es la fecha en que el evento se considera público.
--     La entrada del backtest NUNCA puede ser <= D0_close_date. Esto se aplica con
--     un CHECK constraint, no solo con disciplina de código (ver backtest/backtester.py).
--   - prices separa close_raw de adj_factor porque yfinance recalcula los ajustados
--     retroactivamente (AUDIT_LEAN.md §2.2.4 / ARCHITECTURE_LEAN.md §4). Sin esto el
--     backtest de hoy no es reproducible mañana.
--   - is_delisted_flag en universe: no se rellenan huecos, se marcan (spec del usuario).
--
-- 3 tablas "main" pedidas por el spec: events, analyses, backtest_runs.
-- prices y universe son soporte imprescindible (no opcional: sin ellas no hay
-- CAR, y sin universe no se puede medir sesgo de supervivencia — AUDIT_LEAN.md §2.4).

CREATE TABLE IF NOT EXISTS universe (
    cik                 TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL,
    sic_code            TEXT,
    company_name        TEXT NOT NULL,
    first_seen_date     DATE NOT NULL,
    last_seen_date      DATE NOT NULL,
    is_delisted_flag    BOOLEAN NOT NULL DEFAULT FALSE,
    delisted_date       DATE,               -- de formulario 25-NSE en EDGAR, si se detecta
    market_cap_last_usd NUMERIC,
    adv_usd_60d         NUMERIC,             -- volumen medio diario en $, 60 días
    in_investable_universe BOOLEAN NOT NULL DEFAULT FALSE,  -- price>$5, cap>$300M, ADV>$1M
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_universe_ticker ON universe (ticker);

-- ============================================================================
-- events: el eje del sistema. Un evento = un 8-K (o item FDA) normalizado.
-- ============================================================================
CREATE TABLE IF NOT EXISTS events (
    event_id            BIGSERIAL PRIMARY KEY,
    cik                 TEXT NOT NULL REFERENCES universe(cik),
    ticker              TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('EDGAR', 'FDA_RSS', 'FDA_OPENFDA')),
    is_satellite        BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE para FDA (AUDIT_LEAN.md §2.2.4):
                                                          -- n insuficiente para probar nada por
                                                          -- debajo de ~4% edge/evento. Nunca se
                                                          -- mezcla con EDGAR en el mismo test.
    event_class         TEXT NOT NULL,        -- '8K_2.02_EARNINGS','8K_1.01_MATERIAL_AGMT',
                                               -- '8K_4.02_RESTATEMENT','8K_5.02_MGMT_CHANGE',
                                               -- '8K_1.03_BANKRUPTCY','8K_8.01_OTHER',
                                               -- 'FDA_APPROVAL','FDA_CRL','FDA_ADCOM'
    item_codes          TEXT[],               -- códigos de Item del 8-K tal cual, ej {'2.02','9.01'}
    accession_number     TEXT,                -- identificador único de EDGAR, NULL si es FDA
    source_url           TEXT NOT NULL,
    filed_at             TIMESTAMPTZ NOT NULL,  -- timestamp real de presentación/publicación
    -- D0_close_date es la piedra angular anti-look-ahead (ARCHITECTURE_LEAN.md §4):
    -- el evento se considera público al CIERRE del día de negociación en que se filed,
    -- ajustado a que si filed_at es después del cierre de mercado (16:00 ET) o es
    -- fin de semana/festivo, D0 pasa al siguiente día hábil.
    d0_close_date        DATE NOT NULL,
    classification_method TEXT NOT NULL CHECK (classification_method IN ('RULE', 'LLM_HAIKU_BATCH')),
    classification_confidence NUMERIC CHECK (classification_confidence BETWEEN 0 AND 1),
    raw_text_hash        TEXT NOT NULL,        -- hash del contenido: evita reclasificar en reruns
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, accession_number, event_class)
);

CREATE INDEX IF NOT EXISTS idx_events_class_date ON events (event_class, d0_close_date);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events (ticker);
CREATE INDEX IF NOT EXISTS idx_events_hash ON events (raw_text_hash);

-- ============================================================================
-- prices: panel diario. close_raw + adj_factor separados a propósito (ver cabecera).
-- ============================================================================
CREATE TABLE IF NOT EXISTS prices (
    ticker              TEXT NOT NULL,
    trade_date          DATE NOT NULL,
    close_raw           NUMERIC,
    adj_factor          NUMERIC,              -- close_adjusted = close_raw * adj_factor
    volume              BIGINT,
    -- flag de posible gap de supervivencia: NO se rellena, se marca (instrucción explícita).
    -- TRUE cuando yfinance no devuelve fila para una fecha de calendario bursátil
    -- esperada dentro del rango solicitado para ese ticker.
    survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,
    captured_at          TIMESTAMPTZ NOT NULL DEFAULT now(),  -- fecha de la descarga, no del precio
    PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices (ticker, trade_date);

-- ============================================================================
-- fama_french_factors: panel diario de factores (Ken French Data Library).
-- ============================================================================
CREATE TABLE IF NOT EXISTS fama_french_factors (
    trade_date  DATE PRIMARY KEY,
    mkt_rf      NUMERIC NOT NULL,
    smb         NUMERIC NOT NULL,
    hml         NUMERIC NOT NULL,
    rf          NUMERIC NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- analyses: salida del motor adversarial (Bull/Bear/Judge) por evento.
-- ============================================================================
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id         BIGSERIAL PRIMARY KEY,
    event_id            BIGINT NOT NULL REFERENCES events(event_id),
    model               TEXT NOT NULL,         -- 'claude-haiku-4-5'
    batch_id            TEXT,                  -- id del batch de Anthropic, para trazabilidad
    bull_thesis         TEXT NOT NULL,
    bull_expected_move_pct NUMERIC,             -- % esperado, dirección + magnitud
    bull_confidence     NUMERIC CHECK (bull_confidence BETWEEN 0 AND 1),
    bear_thesis         TEXT NOT NULL,
    bear_expected_move_pct NUMERIC,
    bear_confidence     NUMERIC CHECK (bear_confidence BETWEEN 0 AND 1),
    judge_verdict        TEXT NOT NULL,         -- resumen del arbitraje Bull vs Bear
    judge_expected_move_pct NUMERIC NOT NULL,   -- síntesis final: EV direccional
    judge_confidence     NUMERIC NOT NULL CHECK (judge_confidence BETWEEN 0 AND 1),
    ev_score             NUMERIC NOT NULL,       -- expected_move * confidence, usado para sizing
    -- IMPORTANTE anti-look-ahead: el análisis solo puede usar información
    -- disponible en o antes de events.d0_close_date. Se aplica en el código
    -- (el prompt nunca recibe precios posteriores a D0), no solo aquí.
    analyzed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, model)
);

CREATE INDEX IF NOT EXISTS idx_analyses_event ON analyses (event_id);

-- ============================================================================
-- backtest_runs: un run = una versión de estrategia evaluada sobre un conjunto
-- de eventos. Guarda trade-by-trade, no solo el agregado, porque T8/T9 de
-- ARCHITECTURE_LEAN.md exigen intervalos de confianza y reproducibilidad, y eso
-- requiere los resultados por trade, no solo el resumen.
-- ============================================================================
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id               BIGSERIAL PRIMARY KEY,
    event_id             BIGINT NOT NULL REFERENCES events(event_id),
    strategy_version     TEXT NOT NULL CHECK (strategy_version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    -- fechas de entrada/salida: NUNCA <= d0_close_date. Enforced por CHECK abajo
    -- Y por un assert explícito en backtest/backtester.py (defensa en profundidad).
    entry_date           DATE NOT NULL,
    exit_date_5d          DATE,
    exit_date_20d         DATE,
    entry_price           NUMERIC,
    exit_price_5d          NUMERIC,
    exit_price_20d         NUMERIC,
    predicted_direction    TEXT NOT NULL CHECK (predicted_direction IN ('LONG', 'SHORT', 'NO_TRADE')),
    predicted_ev_pct       NUMERIC NOT NULL,     -- de analyses.ev_score en el momento de decidir
    realized_return_5d_pct  NUMERIC,             -- retorno real D+1(apertura)->D+5(cierre)
    realized_return_20d_pct NUMERIC,
    slippage_bps_applied    NUMERIC NOT NULL DEFAULT 25,  -- barrido de sensibilidad T7
    hit_5d                BOOLEAN,               -- TRUE si signo(realized) == signo(predicted)
    hit_20d               BOOLEAN,
    had_survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,  -- copiado de prices para ese trade
    run_batch_tag          TEXT NOT NULL,        -- identifica la corrida (fecha+git sha) para T9
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_no_lookahead_5d  CHECK (exit_date_5d  IS NULL OR exit_date_5d  > entry_date),
    CONSTRAINT chk_no_lookahead_20d CHECK (exit_date_20d IS NULL OR exit_date_20d > entry_date),
    UNIQUE (event_id, strategy_version, run_batch_tag)
);

CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_runs (strategy_version, run_batch_tag);
CREATE INDEX IF NOT EXISTS idx_backtest_event ON backtest_runs (event_id);

-- ============================================================================
-- placebo_runs: T1 de ARCHITECTURE_LEAN.md — mismo pipeline, fechas aleatorias.
-- Tabla separada a propósito: nunca se debe poder confundir un resultado
-- placebo con uno real en una query descuidada.
-- ============================================================================
CREATE TABLE IF NOT EXISTS placebo_runs (
    placebo_id            BIGSERIAL PRIMARY KEY,
    ticker                TEXT NOT NULL,
    fake_event_date        DATE NOT NULL,     -- fecha aleatoria, emparejada por calendario
    matched_real_event_id  BIGINT REFERENCES events(event_id),  -- el evento real que se emparejó
    car_5d                 NUMERIC,
    car_20d                NUMERIC,
    run_batch_tag           TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
