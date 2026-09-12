// queries.ts — lecturas tipadas contra el schema de pipeline/db/schema.sql.
//
// Los nombres de campo aquí deben mantenerse en sincronía con ese fichero;
// no hay una capa de migración/ORM que lo garantice automáticamente en este
// POC (decisión deliberada de simplicidad — ver ARCHITECTURE_LEAN.md §2).
//
// El dashboard (Fase 5) no recalcula NINGUNA fórmula financiera del lado de
// TypeScript: lee report_json ya armado por portfolio_report.py y
// paper_trading/report.py (Sharpe/Sortino/Calmar, calibración, submétricas
// por tipo de evento, etc.) — reimplementar esas fórmulas aquí sería
// mantener dos fuentes de verdad para el mismo número.
import { getPool } from "./db";

export type StrategyVersion = "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE";

export interface PortfolioTradeMetrics {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_winner: number | null;
  avg_loser: number | null;
  expectancy: number | null;
  largest_win: number | null;
  largest_loss: number | null;
  consecutive_wins: number;
  consecutive_losses: number;
}

export interface PortfolioEquityMetrics {
  total_return: number | null;
  annual_return: number | null;
  max_drawdown: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  recovery_factor: number | null;
  final_balance: number | null;
}

export interface PortfolioEquityPoint {
  trade_date: string;
  balance: number;
}

export interface EventTypeMetric {
  event_type: string;
  n_trades: number;
  win_rate: number;
  avg_return: number;
  sharpe_per_trade: number | null;
  insufficient_sample: boolean;
}

export interface Calibration {
  n_trades: number;
  mean_predicted_pct: number | null;
  mean_actual_pct: number | null;
  calibration_score: number | null;
  meets_target: boolean | null;
}

export interface PredictionRegression {
  n_trades: number;
  r_squared: number | null;
  scatter: { predicted: number; actual: number }[];
}

export interface AsymmetryReport {
  n_trades: number;
  threshold_pct?: number;
  pct_reaching_positive_threshold: number | null;
  pct_reaching_negative_threshold: number | null;
  asymmetric_favoring_gains: boolean | null;
}

export interface SerializedPortfolioTrade {
  event_id: number;
  ticker: string | null;
  event_class: string | null;
  direction: string;
  entry_date: string;
  exit_date: string;
  exit_reason: string;
  pnl_pct: number;
  confidence: number;
  ev: number;
}

export interface ConfidenceBucket {
  bucket: string;
  confidence_min: number;
  confidence_max: number;
  n: number;
  hit_rate: number;
  mean_confidence: number;
}

export interface CalibrationDiagnostics {
  n: number;
  correlation: number | null;
  brier_score: number | null;
  ece: number | null;
  buckets: ConfidenceBucket[];
}

export interface TemporalStability {
  split_date: string;
  n_trades_before: number;
  n_trades_after: number;
  metrics_before: PortfolioTradeMetrics;
  metrics_after: PortfolioTradeMetrics;
  stable: boolean | null;
  warnings: string[];
}

export interface PortfolioVersionReport {
  version: StrategyVersion;
  run_batch_tag: string;
  trade_metrics: PortfolioTradeMetrics;
  equity_metrics: PortfolioEquityMetrics;
  equity_curve: PortfolioEquityPoint[];
  metrics_by_event_type: Record<string, EventTypeMetric>;
  confidence_calibration: CalibrationDiagnostics;
  calibration: Calibration;
  prediction_regression: PredictionRegression;
  asymmetry: AsymmetryReport;
  all_trades: SerializedPortfolioTrade[];
  top_10_winners: SerializedPortfolioTrade[];
  top_10_losers: SerializedPortfolioTrade[];
  no_lookahead_violations: string[];
  temporal_stability: TemporalStability | null;
}

export interface PortfolioBiasReport {
  n_total_tickers: number;
  n_delisted: number;
  survivorship_bias_pct: number | null;
  n_price_gaps: number;
  n_price_rows: number;
  data_gap_pct: number | null;
}

export interface PortfolioRecommendation {
  verdict: string;
  findings: string[];
}

export interface PortfolioReport {
  run_batch_tag: string;
  starting_capital: number;
  versions: Record<StrategyVersion, PortfolioVersionReport>;
  bias_report: PortfolioBiasReport;
  recommendation: PortfolioRecommendation;
}

/** El run_batch_tag más reciente con un reporte de cartera guardado
 * (portfolio_reports, backtest histórico completo — Fase 3/4). */
export async function getLatestPortfolioRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT run_batch_tag FROM portfolio_reports ORDER BY created_at DESC LIMIT 1`
  );
  return rows[0]?.run_batch_tag ?? null;
}

/** El reporte completo para un run_batch_tag — node-postgres deserializa
 * JSONB directamente a objeto JS con los números ya como number (Python los
 * serializó con json.dumps sobre floats, no son columnas NUMERIC de
 * Postgres, así que no hace falta parseFloat manual aquí). */
export async function getPortfolioReport(runBatchTag: string): Promise<PortfolioReport | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT report_json FROM portfolio_reports WHERE run_batch_tag = $1`,
    [runBatchTag]
  );
  return rows[0]?.report_json ?? null;
}

// ============================================================================
// Paper trading (Fase 4 — pipeline/paper_trading/report.py)
// ============================================================================

export interface PaperOpenPosition {
  event_id: number;
  ticker: string;
  event_class: string | null;
  direction: string;
  entry_date: string;
  entry_price: number;
  current_price: number | null;
  current_price_date: string | null;
  unrealized_pnl_pct: number | null;
}

export interface PaperClosedTrade {
  event_id: number;
  ticker: string;
  event_class: string | null;
  direction: string;
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  exit_reason: string;
  status: string;
  pnl_pct: number;
}

export interface PaperPrediction {
  event_id: number;
  ticker: string;
  event_class: string | null;
  predicted_direction: string;
  predicted_magnitude: number;
  actual_move_5d: number | null;
  error: number | null;
  was_correct: boolean | null;
  confidence_given: number;
  calibration_check: string;
  was_traded: boolean;
}

export interface PaperAlert {
  type: "WARNING" | "CONGRATULATE";
  version: StrategyVersion;
  message: string;
  ticker?: string;
  event_id?: number;
}

export interface PaperBacktestComparison {
  available: boolean;
  comparable?: boolean;
  note?: string;
  historical_win_rate?: number;
  week_win_rate?: number;
  diff_pp?: number;
  matches_historical?: boolean;
}

export interface PaperVersionReport {
  version: StrategyVersion;
  n_open_positions: number;
  n_closed_trades: number;
  open_positions: PaperOpenPosition[];
  last_10_closed_trades: PaperClosedTrade[];
  cumulative_pnl_pct_week: number;
  trade_metrics: PortfolioTradeMetrics;
  predictions: PaperPrediction[];
  calibration: CalibrationDiagnostics;
  alerts: PaperAlert[];
  comparison_with_historical_backtest: PaperBacktestComparison;
  top_3_best_predicted: PaperPrediction[];
  top_3_worst_predicted: PaperPrediction[];
}

export interface PaperTradingReport {
  run_batch_tag: string;
  week_start: string;
  week_end: string;
  versions: Record<StrategyVersion, PaperVersionReport>;
}

/** El run_batch_tag más reciente con un reporte de paper trading guardado.
 * A diferencia de portfolio_reports, aquí el mismo tag se reutiliza noche
 * tras noche mientras dure la semana simulada (paper_trading/report.py:
 * run_batch_tag se deriva de la semana, no de la fecha de hoy) — por eso
 * "más reciente por created_at" sigue siendo correcto: cada UPDATE
 * refresca created_at. */
export async function getLatestPaperTradingRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT run_batch_tag FROM paper_trading_reports ORDER BY created_at DESC LIMIT 1`
  );
  return rows[0]?.run_batch_tag ?? null;
}

export async function getPaperTradingReport(runBatchTag: string): Promise<PaperTradingReport | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT report_json FROM paper_trading_reports WHERE run_batch_tag = $1`,
    [runBatchTag]
  );
  return rows[0]?.report_json ?? null;
}

// ============================================================================
// Tab 1 "All Signals" (Fase 5) — feed cronológico de eventos analizados,
// con o sin trade_decision, con filtros. A diferencia del resto de este
// archivo, esta query SÍ compone datos con SQL propio (no lee un
// report_json ya armado) porque no hay un reporte pre-calculado que cubra
// "todos los eventos alguna vez analizados" — portfolio_reports solo cubre
// los que SÍ se operaron en la versión BALANCED del backtest más reciente.
// ============================================================================

export interface SignalFeedFilters {
  ticker?: string;
  eventClass?: string;
  signal?: "LONG" | "SHORT" | "NO_TRADE";
  dateFrom?: string;
  dateTo?: string;
  minConfidence?: number;
  limit?: number;
}

export interface SignalFeedRow {
  event_id: number;
  ticker: string;
  event_class: string;
  source: string;
  d0_close_date: string;
  novelty_score: number;
  bull_output: unknown;
  bear_output: unknown;
  judge_output: unknown;
  net_conviction: number;
  confidence: number;
  ev_balanced: number;
  signal: "LONG" | "SHORT" | "NO_TRADE";
  trade_decision_conservative: string;
  trade_decision_aggressive: string;
  trade_decision_balanced: string;
  entry_date: string | null;
  exit_date: string | null;
  exit_reason: string | null;
  pnl_pct: number | null;
}

export async function getEventClasses(): Promise<string[]> {
  const pool = getPool();
  const { rows } = await pool.query(`SELECT DISTINCT event_class FROM events ORDER BY event_class`);
  return rows.map((r) => r.event_class);
}

export async function getSignalsFeed(filters: SignalFeedFilters): Promise<SignalFeedRow[]> {
  const pool = getPool();
  const conditions: string[] = [];
  const params: unknown[] = [];

  if (filters.ticker) {
    params.push(`%${filters.ticker.toUpperCase()}%`);
    conditions.push(`e.ticker ILIKE $${params.length}`);
  }
  if (filters.eventClass) {
    params.push(filters.eventClass);
    conditions.push(`e.event_class = $${params.length}`);
  }
  if (filters.dateFrom) {
    params.push(filters.dateFrom);
    conditions.push(`e.d0_close_date >= $${params.length}`);
  }
  if (filters.dateTo) {
    params.push(filters.dateTo);
    conditions.push(`e.d0_close_date <= $${params.length}`);
  }
  if (filters.minConfidence !== undefined) {
    params.push(filters.minConfidence);
    conditions.push(`ea.confidence_in_conviction >= $${params.length}`);
  }
  if (filters.signal) {
    params.push(filters.signal);
    conditions.push(`signal.value = $${params.length}`);
  }

  params.push(filters.limit ?? 200);
  const limitParam = `$${params.length}`;

  const { rows } = await pool.query(
    `
    SELECT * FROM (
      SELECT
        e.event_id, e.ticker, e.event_class, e.source, e.d0_close_date,
        ea.novelty_score, ea.bull_analyst_output AS bull_output, ea.bear_analyst_output AS bear_output,
        ea.judge_output, ea.net_conviction, ea.confidence_in_conviction AS confidence, ea.ev_balanced,
        ea.trade_decision_conservative, ea.trade_decision_aggressive, ea.trade_decision_balanced,
        (CASE
          WHEN ea.trade_decision_conservative = 'NO_TRADE' AND ea.trade_decision_aggressive = 'NO_TRADE'
               AND ea.trade_decision_balanced = 'NO_TRADE' THEN 'NO_TRADE'
          WHEN ea.net_conviction > 0 THEN 'LONG'
          ELSE 'SHORT'
        END) AS value,
        pt.entry_date, pt.exit_date, pt.exit_reason, pt.pnl_pct
      FROM events e
      JOIN event_analyses ea ON ea.event_id = e.event_id
      LEFT JOIN portfolio_trades pt ON pt.event_id = e.event_id AND pt.version = 'BALANCED'
    ) signal
    ${conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : ""}
    ORDER BY signal.d0_close_date DESC
    LIMIT ${limitParam}
    `,
    params
  );

  return rows.map((r) => ({
    event_id: r.event_id,
    ticker: r.ticker,
    event_class: r.event_class,
    source: r.source,
    d0_close_date: r.d0_close_date instanceof Date ? r.d0_close_date.toISOString().slice(0, 10) : r.d0_close_date,
    novelty_score: r.novelty_score,
    bull_output: r.bull_output,
    bear_output: r.bear_output,
    judge_output: r.judge_output,
    net_conviction: parseFloat(r.net_conviction),
    confidence: parseFloat(r.confidence),
    ev_balanced: parseFloat(r.ev_balanced),
    signal: r.value,
    trade_decision_conservative: r.trade_decision_conservative,
    trade_decision_aggressive: r.trade_decision_aggressive,
    trade_decision_balanced: r.trade_decision_balanced,
    entry_date: r.entry_date instanceof Date ? r.entry_date.toISOString().slice(0, 10) : r.entry_date,
    exit_date: r.exit_date instanceof Date ? r.exit_date.toISOString().slice(0, 10) : r.exit_date,
    exit_reason: r.exit_reason,
    pnl_pct: r.pnl_pct !== null ? parseFloat(r.pnl_pct) : null,
  }));
}
