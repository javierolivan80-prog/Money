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
-- 3 tablas "main" pedidas por el spec de Fase 1: events, analyses, backtest_runs.
-- prices y universe son soporte imprescindible (no opcional: sin ellas no hay
-- CAR, y sin universe no se puede medir sesgo de supervivencia — AUDIT_LEAN.md §2.4).
--
-- FASE 2 (Análisis de eventos) añade event_enrichment (Etapa 1) y
-- event_analyses (Etapas 2-8: novelty, Bull/Bear/Judge, impact, EV,
-- abstention), que sustituye a la tabla `analyses` de la Fase 1 — ver la
-- cabecera de event_analyses más abajo para el porqué del reemplazo.

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

-- Columnas de Fase 3 (ingest/filing_text.py), vía ALTER — CREATE TABLE
-- IF NOT EXISTS no las habría añadido a una tabla `events` ya existente
-- (misma lección aprendida en la Fase 2 con prices.high_raw/low_raw: ver ahí
-- para el porqué). NULL hasta que el backfill de texto corra sobre el evento;
-- eventos FDA (source != 'EDGAR') se quedan en NULL permanentemente hasta que
-- exista un scraper de FDA — ver filing_text.py:populate_missing_filing_text.
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_length_chars INT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_includes_exhibit BOOLEAN;
ALTER TABLE events ADD COLUMN IF NOT EXISTS filing_text_extracted_at TIMESTAMPTZ;

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

-- Columnas añadidas en Fase 2, vía ALTER en vez de en el CREATE TABLE de
-- arriba: `CREATE TABLE IF NOT EXISTS` es un no-op silencioso sobre una tabla
-- que ya existe, así que reaplicar schema.sql en un despliegue que ya corrió
-- la Fase 1 NUNCA habría añadido estas columnas (se encontró al reaplicar el
-- schema sobre el Postgres local de esta sesión, que ya tenía `prices` de la
-- Fase 1 — no en una tabla nueva, donde el problema pasa desapercibido).
-- `ADD COLUMN IF NOT EXISTS` sí es idempotente en Postgres 9.6+.
ALTER TABLE prices ADD COLUMN IF NOT EXISTS high_raw NUMERIC;  -- proxy de spread/liquidez:
ALTER TABLE prices ADD COLUMN IF NOT EXISTS low_raw NUMERIC;   -- (high-low)/close — ver
                                                                -- abstention_engine.py. No es
                                                                -- bid-ask real: no hay datos de
                                                                -- microestructura gratis
                                                                -- (AUDIT_LEAN.md §2.1). Proxy
                                                                -- documentado, no ocultado.

-- Añadida para el backtest de cartera (backtest/portfolio_simulator.py):
-- la entrada real es "apertura D+1", y hasta ahora `prices` solo guardaba
-- close/high/low — ningún backfill había pedido nunca el precio de apertura
-- porque compute_car() y el backtest simple de la Fase 1 solo usan cierres.
ALTER TABLE prices ADD COLUMN IF NOT EXISTS open_raw NUMERIC;

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
-- car_results: retorno anormal acumulado (CAR) por evento y ventana,
-- calculado por backtest/backtester.py:compute_car(). FALTABA en la Fase 1:
-- compute_car() devolvía el resultado en memoria pero nada lo persistía —
-- sin esta tabla, "historical analogues ya calculados" (Etapa 6 de la Fase 2)
-- no tenía nada que leer. Se añade aquí porque analyze/historical_analogues.py
-- la necesita, no como limpieza de la Fase 1 en sí, pero de paso cierra ese
-- hueco (documentado en RUNBOOK.md).
-- ============================================================================
CREATE TABLE IF NOT EXISTS car_results (
    event_id             BIGINT NOT NULL REFERENCES events(event_id),
    window_days           INT NOT NULL,        -- 5 o 20, coincide con backtest/backtester.py
    car                   NUMERIC NOT NULL,     -- fracción, no % (0.05 = 5%)
    abnormal_volume_ratio  NUMERIC,
    n_estimation_days      INT NOT NULL,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, window_days)
);

-- Soporta la query de analogues: por clase, ordenado por fecha, excluyendo
-- eventos futuros respecto al evento que se está evaluando (ver
-- analyze/historical_analogues.py — es la misma disciplina anti-look-ahead
-- del resto del proyecto, aplicada a la ventana de análogos históricos).
CREATE INDEX IF NOT EXISTS idx_car_results_class_lookup ON events (event_class, d0_close_date, event_id);

-- ============================================================================
-- event_enrichment: salida de la Etapa 1 (Fase 2) — features de mercado por
-- evento, calculadas UNA VEZ y reutilizadas por novelty/impact/EV, en vez de
-- recalcularlas en cada etapa. Todo lo que entra aquí usa SOLO datos
-- disponibles en o antes de events.d0_close_date (mismo principio
-- anti-look-ahead que backtest_runs — ver ARCHITECTURE_LEAN.md §4).
-- ============================================================================
CREATE TABLE IF NOT EXISTS event_enrichment (
    event_id             BIGINT PRIMARY KEY REFERENCES events(event_id),
    price_d0             NUMERIC,             -- cierre ajustado del día del evento
    price_d_minus_5      NUMERIC,
    price_d_minus_20     NUMERIC,
    volume_d0            BIGINT,
    volume_avg_20d       NUMERIC,
    volume_ratio         NUMERIC,             -- volume_d0 / volume_avg_20d
    beta_vs_spy          NUMERIC,             -- de la regresión de factores (coef. de mkt_rf)
    ff_size_exposure     NUMERIC,             -- coef. de SMB
    ff_value_exposure    NUMERIC,             -- coef. de HML
    vix_d0               NUMERIC,
    sector_etf_ticker    TEXT,                -- ETF usado como proxy de sector (ver enrichment.py)
    sector_mood          NUMERIC,             -- retorno_sector_d0 - retorno_spy_d0
    pre_event_drift_pct  NUMERIC,             -- retorno D-5 -> D-1, insumo del novelty engine
    high_low_range_pct   NUMERIC,             -- (high-low)/close en D0 — proxy de liquidez/spread
    n_estimation_days    INT,                 -- días usados en la regresión — baja confianza si es poco
    had_survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,  -- copiado de prices, propaga a abstention
    enriched_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- event_analyses: pipeline completo de la Fase 2 (Etapas 2-8), un registro
-- por evento con TRAZA COMPLETA de cada etapa (pedido explícito: "esto
-- permite debugear decisiones malas después"). Sustituye a la tabla `analyses`
-- de la Fase 1 (Bull/Bear/Judge con esquema simple) — nada dependía de esa
-- tabla en producción, así que se reemplaza en vez de mantener dos esquemas
-- paralelos.
--
-- Columnas JSONB para el output completo de cada etapa (auditoría) MÁS
-- columnas planas para los campos que se filtran/agregan a menudo (evita
-- tener que hacer ->> en cada query de stats del día 3).
-- ============================================================================
CREATE TABLE IF NOT EXISTS event_analyses (
    event_id              BIGINT PRIMARY KEY REFERENCES events(event_id),

    -- Etapa 2: Novelty Engine
    novelty_score          NUMERIC NOT NULL CHECK (novelty_score BETWEEN 0 AND 100),
    novelty_reasoning       JSONB NOT NULL,     -- {pre_event_drift, has_guidance, rumor_flag, ...}

    -- Etapas 3-5: Bull / Bear / Judge (JSONB = el JSON exacto que pide el spec)
    bull_analyst_output      JSONB NOT NULL,
    bear_analyst_output      JSONB NOT NULL,
    judge_output             JSONB NOT NULL,
    net_conviction           NUMERIC NOT NULL CHECK (net_conviction BETWEEN -1 AND 1),
    confidence_in_conviction NUMERIC NOT NULL CHECK (confidence_in_conviction BETWEEN 0 AND 100),

    -- Etapa 6: Impact Estimation
    impact_estimation        JSONB NOT NULL,
    n_historical_analogues    INT NOT NULL,     -- tamaño de muestra detrás de impact_estimation —
                                                 -- clave para no confundir confianza con ruido
                                                 -- (AUDIT_LEAN.md §2.2.3, MDE por clase de evento)

    -- Etapa 7: Expected Value Engine
    ev_calculation           JSONB NOT NULL,
    ev_conservative           NUMERIC NOT NULL,
    ev_aggressive             NUMERIC NOT NULL,
    ev_balanced               NUMERIC NOT NULL,

    -- Etapa 8: Abstention Engine (una decisión POR VERSIÓN de estrategia, no una sola global —
    -- el mismo evento puede ser TRADE para Aggressive y NO_TRADE para Conservative)
    abstention_decision       JSONB NOT NULL,    -- {CONSERVATIVE: {...}, AGGRESSIVE: {...}, BALANCED: {...}}
    trade_decision_conservative TEXT NOT NULL CHECK (trade_decision_conservative IN ('LONG','SHORT','NO_TRADE')),
    trade_decision_aggressive   TEXT NOT NULL CHECK (trade_decision_aggressive IN ('LONG','SHORT','NO_TRADE')),
    trade_decision_balanced     TEXT NOT NULL CHECK (trade_decision_balanced IN ('LONG','SHORT','NO_TRADE')),

    -- Metadatos / auditoría (pedido explícito: audit trail completo)
    model_version_bull_bear    TEXT NOT NULL,   -- 'claude-haiku-4-5'
    model_version_judge        TEXT NOT NULL,   -- 'claude-sonnet-4-6'
    batch_id_bull_bear          TEXT,
    batch_id_judge               TEXT,
    from_cache                   BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE si reusó un análisis
                                                                   -- de (ticker,event_class) <24h
    analyzed_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_event_analyses_trade_balanced ON event_analyses (trade_decision_balanced);
CREATE INDEX IF NOT EXISTS idx_event_analyses_analyzed_at ON event_analyses (analyzed_at);

-- Soporta la caché de 24h por (ticker, event_class): busca el análisis más
-- reciente de esa combinación sin tener que escanear toda la tabla.
CREATE INDEX IF NOT EXISTS idx_events_ticker_class_for_cache ON events (ticker, event_class, d0_close_date);

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
    predicted_ev_pct       NUMERIC NOT NULL,     -- de event_analyses.ev_balanced (u otra versión) en el momento de decidir
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

-- ============================================================================
-- portfolio_trades / portfolio_equity_curve: backtest de cartera con gestión
-- de riesgo real (backtest/portfolio_simulator.py) — distinto de
-- backtest_runs (Fase 1), que sigue existiendo y sigue siendo necesario: son
-- dos preguntas distintas (ver AUDIT_LEAN.md §2.2.3 y ARCHITECTURE_LEAN.md §8):
--   - backtest_runs / compute_car(): ¿existe un efecto estadístico? Ventana
--     fija D+1→D+5/D+20, sin gestión de posición — es lo que sostiene T1/T2
--     (placebo y réplica). NO SE TOCA.
--   - portfolio_trades: si de verdad se operara esto con sizing, stops y
--     límites de concurrencia, ¿qué equity curve y qué métricas de riesgo
--     resultarían? Bucle diario con TP/SL/trailing/max-holding real.
--
-- Una posición con trailing stop que cierra en varios tramos (30% aquí, 30%
-- allá) se consolida en UNA fila por posición, no una fila por tramo: el
-- spec pide un registro por trade con un solo entry/exit, así que exit_price
-- y pnl_pct son el promedio ponderado de los tramos, y exit_date es la
-- fecha del último tramo (posición totalmente cerrada). Ver portfolio_simulator.py.
-- ============================================================================
CREATE TABLE IF NOT EXISTS portfolio_trades (
    trade_id               BIGSERIAL PRIMARY KEY,
    event_id               BIGINT NOT NULL REFERENCES events(event_id),
    version                TEXT NOT NULL CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    -- Para BALANCED, cada trade se ejecuta con las reglas de Conservative O
    -- Aggressive (ver portfolio_strategies.py:classify_balanced_execution_style) —
    -- execution_style registra cuál, para poder auditar la mezcla real.
    execution_style         TEXT NOT NULL CHECK (execution_style IN ('CONSERVATIVE', 'AGGRESSIVE')),
    direction               TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_date              DATE NOT NULL,
    entry_price             NUMERIC NOT NULL,
    exit_date               DATE NOT NULL,
    exit_price              NUMERIC NOT NULL,
    exit_reason             TEXT NOT NULL CHECK (exit_reason IN ('TAKE_PROFIT', 'STOP_LOSS', 'MAX_HOLDING', 'TRAILING_STOP')),
    pnl_pct                 NUMERIC NOT NULL,      -- retorno neto de comisiones, con signo
    pnl_abs                 NUMERIC NOT NULL,      -- en $ sobre el tamaño de posición asignado
    position_size_pct       NUMERIC NOT NULL,      -- % de la cartera en el momento de la entrada
    position_size_dollars   NUMERIC NOT NULL,
    confidence              NUMERIC NOT NULL,       -- confidence_in_conviction del Judge, en la decisión
    ev                      NUMERIC NOT NULL,        -- ev_{version} usado para el umbral de entrada
    prediction              NUMERIC NOT NULL,        -- net_conviction del Judge (dirección + fuerza)
    actual_move_pct         NUMERIC NOT NULL,        -- retorno real del SUBYACENTE entry->exit (puede
                                                      -- diferir de pnl_pct: pnl_pct ya lleva comisiones
                                                      -- y el efecto de cierres parciales por trailing stop)
    had_survivorship_warning BOOLEAN NOT NULL DEFAULT FALSE,
    run_batch_tag            TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Checksums anti-look-ahead (spec Fase Backtesting): la entrada nunca es
    -- D0, y la salida nunca es anterior o igual a la entrada.
    CONSTRAINT chk_portfolio_no_lookahead CHECK (exit_date > entry_date),
    -- Sin esto, reejecutar simulate_portfolio() con el MISMO run_batch_tag
    -- (ej. un re-disparo manual del workflow el mismo día) duplicaría cada
    -- trade — mismo patrón que backtest_runs (Fase 1), que sí lo tenía desde
    -- el principio; se encontró la falta al revisar la idempotencia antes
    -- de cablear esto al cron nocturno.
    UNIQUE (event_id, version, run_batch_tag)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_trades_version_tag ON portfolio_trades (version, run_batch_tag);
CREATE INDEX IF NOT EXISTS idx_portfolio_trades_event ON portfolio_trades (event_id);

-- La UNIQUE de arriba (dentro del CREATE TABLE) solo llega a una instalación
-- NUEVA — CREATE TABLE IF NOT EXISTS es un no-op sobre una tabla que ya
-- existe, y a diferencia de una columna, Postgres no soporta
-- "ADD CONSTRAINT IF NOT EXISTS" de forma nativa. Se encontró exactamente
-- este caso al añadir la constraint sobre el Postgres de esta sesión, que ya
-- tenía portfolio_trades de antes: un bloque DO condicional es la forma
-- correcta de hacerlo idempotente (misma lección que high_raw/low_raw y
-- filing_text — ver más arriba en este fichero).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'portfolio_trades_event_id_version_run_batch_tag_key'
    ) THEN
        ALTER TABLE portfolio_trades ADD CONSTRAINT portfolio_trades_event_id_version_run_batch_tag_key
            UNIQUE (event_id, version, run_batch_tag);
    END IF;
END $$;

-- Curva de equity DIARIA (no solo en días de trade): balance = cash +
-- valor a mercado de las posiciones abiertas ese día, marcado con el cierre
-- del día. Es lo que pide el spec ("Plotea balance(date) para 5 años").
CREATE TABLE IF NOT EXISTS portfolio_equity_curve (
    version         TEXT NOT NULL CHECK (version IN ('CONSERVATIVE', 'AGGRESSIVE', 'BALANCED')),
    trade_date      DATE NOT NULL,
    balance         NUMERIC NOT NULL,
    n_open_positions INT NOT NULL DEFAULT 0,
    run_batch_tag    TEXT NOT NULL,
    PRIMARY KEY (version, trade_date, run_batch_tag)
);
