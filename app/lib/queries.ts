// queries.ts — lecturas tipadas contra el schema de pipeline/db/schema.sql.
//
// Los nombres de campo aquí deben mantenerse en sincronía con ese fichero;
// no hay una capa de migración/ORM que lo garantice automáticamente en este
// POC (decisión deliberada de simplicidad — ver ARCHITECTURE_LEAN.md §2).
import { getPool } from "./db";

export type StrategyVersion = "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE";

export interface StrategySummary {
  strategy_version: StrategyVersion;
  n_trades: number;
  win_rate: number | null;
  mean_return_pct: number | null;
  sharpe_annualized: number | null;
  max_drawdown_pct: number | null;
  calibration_corr: number | null;
}

export interface EquityPoint {
  entry_date: string;
  cumulative_return_pct: number;
}

export interface TradeRow {
  event_id: number;
  ticker: string;
  event_class: string;
  entry_date: string;
  exit_date_20d: string | null;
  predicted_direction: string;
  predicted_ev_pct: number;
  realized_return_20d_pct: number | null;
  hit_20d: boolean | null;
  had_survivorship_warning: boolean;
}

const STRATEGIES: StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

/** Resumen agregado por versión de estrategia para el run_batch_tag más reciente.
 * Calculado en SQL directamente (no replica pipeline/backtest/backtester.py:
 * summarize_run, que corre en Python durante el backtest y guarda trade-by-trade
 * aquí — esta query solo agrega lo ya guardado, para que el dashboard no tenga
 * que reimplementar el bootstrap de IC95% del lado de Python; ver nota abajo). */
export async function getStrategySummaries(runBatchTag: string): Promise<StrategySummary[]> {
  const pool = getPool();
  const results: StrategySummary[] = [];
  for (const strategy of STRATEGIES) {
    const { rows } = await pool.query(
      `
      SELECT
        $1::text AS strategy_version,
        count(*)::int AS n_trades,
        avg(CASE WHEN hit_20d THEN 1.0 ELSE 0.0 END) AS win_rate,
        avg(realized_return_20d_pct) AS mean_return_pct,
        stddev(realized_return_20d_pct) AS stddev_return_pct,
        corr(predicted_ev_pct, realized_return_20d_pct) AS calibration_corr
      FROM backtest_runs
      WHERE strategy_version = $1 AND run_batch_tag = $2 AND realized_return_20d_pct IS NOT NULL
      `,
      [strategy, runBatchTag]
    );
    const row = rows[0];
    const stddev = row.stddev_return_pct ? parseFloat(row.stddev_return_pct) : null;
    const mean = row.mean_return_pct ? parseFloat(row.mean_return_pct) : null;
    // Sharpe anualizado aproximado a partir de la ventana de 20 días hábiles.
    // NOTA: esto es una aproximación simple para mostrar en el dashboard, no
    // sustituye al cálculo con bootstrap de pipeline/backtest/backtester.py
    // (summarize_run), que es la fuente de verdad para el reporte final.
    const sharpe = stddev && stddev > 0 && mean !== null ? (mean / stddev) * Math.sqrt(252 / 20) : null;

    results.push({
      strategy_version: strategy,
      n_trades: parseInt(row.n_trades, 10),
      win_rate: row.win_rate ? parseFloat(row.win_rate) : null,
      mean_return_pct: mean,
      sharpe_annualized: sharpe,
      max_drawdown_pct: await getMaxDrawdown(strategy, runBatchTag),
      calibration_corr: row.calibration_corr ? parseFloat(row.calibration_corr) : null,
    });
  }
  return results;
}

async function getMaxDrawdown(strategy: StrategyVersion, runBatchTag: string): Promise<number | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `
    WITH ordered AS (
      SELECT entry_date, realized_return_20d_pct,
             SUM(realized_return_20d_pct) OVER (ORDER BY entry_date) AS cum_return
      FROM backtest_runs
      WHERE strategy_version = $1 AND run_batch_tag = $2 AND realized_return_20d_pct IS NOT NULL
    ),
    running_max AS (
      SELECT entry_date, cum_return, MAX(cum_return) OVER (ORDER BY entry_date) AS peak
      FROM ordered
    )
    SELECT MIN(cum_return - peak) AS max_drawdown FROM running_max
    `,
    [strategy, runBatchTag]
  );
  return rows[0]?.max_drawdown !== null && rows[0]?.max_drawdown !== undefined
    ? parseFloat(rows[0].max_drawdown)
    : null;
}

/** Curva de equity acumulada, ordenada por fecha de entrada — para el SVG del dashboard. */
export async function getEquityCurve(strategy: StrategyVersion, runBatchTag: string): Promise<EquityPoint[]> {
  const pool = getPool();
  const { rows } = await pool.query(
    `
    SELECT entry_date,
           SUM(realized_return_20d_pct) OVER (ORDER BY entry_date) AS cumulative_return_pct
    FROM backtest_runs
    WHERE strategy_version = $1 AND run_batch_tag = $2 AND realized_return_20d_pct IS NOT NULL
    ORDER BY entry_date
    `,
    [strategy, runBatchTag]
  );
  return rows.map((r) => ({
    entry_date: r.entry_date instanceof Date ? r.entry_date.toISOString().slice(0, 10) : r.entry_date,
    cumulative_return_pct: parseFloat(r.cumulative_return_pct),
  }));
}

/** Detalle de trades individuales — necesario para T8/T9 (ARCHITECTURE_LEAN.md
 * §8): reproducibilidad e intervalos de confianza requieren los datos por
 * trade, no solo el agregado. */
export async function getTrades(strategy: StrategyVersion, runBatchTag: string, limit = 100): Promise<TradeRow[]> {
  const pool = getPool();
  const { rows } = await pool.query(
    `
    SELECT b.event_id, e.ticker, e.event_class, b.entry_date, b.exit_date_20d,
           b.predicted_direction, b.predicted_ev_pct, b.realized_return_20d_pct,
           b.hit_20d, b.had_survivorship_warning
    FROM backtest_runs b
    JOIN events e ON e.event_id = b.event_id
    WHERE b.strategy_version = $1 AND b.run_batch_tag = $2
    ORDER BY b.entry_date DESC
    LIMIT $3
    `,
    [strategy, runBatchTag, limit]
  );
  // node-postgres devuelve columnas NUMERIC como string (para no perder
  // precisión al mapear a JS number) — hay que convertir explícitamente, o
  // .toFixed() en el componente revienta con "not a function" (encontrado al
  // probar el dashboard con datos reales, no solo con el build de TypeScript,
  // que no detecta este tipo de desajuste en tiempo de ejecución del driver).
  return rows.map((r) => ({
    ...r,
    entry_date: r.entry_date instanceof Date ? r.entry_date.toISOString().slice(0, 10) : r.entry_date,
    exit_date_20d: r.exit_date_20d instanceof Date ? r.exit_date_20d.toISOString().slice(0, 10) : r.exit_date_20d,
    predicted_ev_pct: parseFloat(r.predicted_ev_pct),
    realized_return_20d_pct: r.realized_return_20d_pct !== null ? parseFloat(r.realized_return_20d_pct) : null,
  }));
}

/** El run_batch_tag más reciente disponible — el dashboard siempre muestra
 * la última corrida, nunca mezcla corridas distintas (rompería T9,
 * reproducibilidad — ver ARCHITECTURE_LEAN.md §8). */
export async function getLatestRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT run_batch_tag FROM backtest_runs ORDER BY created_at DESC LIMIT 1`
  );
  return rows[0]?.run_batch_tag ?? null;
}

// ============================================================================
// Backtest de cartera (Fase 4 — pipeline/backtest/portfolio_report.py)
//
// A diferencia de getStrategySummaries/getEquityCurve arriba (que agregan
// backtest_runs con SQL directamente en el dashboard), aquí el dashboard NO
// recalcula nada: lee portfolio_reports.report_json, que ya trae el reporte
// completo armado por run_full_backtest() — Sharpe/Sortino/Calmar,
// calibración, submétricas por tipo de evento, etc. Reimplementar esas
// fórmulas en TypeScript sería la misma trampa que evita el comentario de
// getStrategySummaries sobre no reimplementar el bootstrap de Python aquí.
// ============================================================================

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
  entry_date: string;
  exit_date: string;
  exit_reason: string;
  pnl_pct: number;
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
  calibration: Calibration;
  prediction_regression: PredictionRegression;
  asymmetry: AsymmetryReport;
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

/** El run_batch_tag más reciente con un reporte de cartera guardado — igual
 * que getLatestRunBatchTag(), pero sobre portfolio_reports (Fase 4), que es
 * una tabla y un pipeline de escritura distintos de backtest_runs (Fase 1). */
export async function getLatestPortfolioRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT run_batch_tag FROM portfolio_reports ORDER BY created_at DESC LIMIT 1`
  );
  return rows[0]?.run_batch_tag ?? null;
}

/** El reporte completo para un run_batch_tag — node-postgres deserializa
 * JSONB directamente a objeto JS, así que a diferencia de getTrades() arriba
 * no hace falta parseFloat manual: los números ya llegaron como number
 * porque Python los serializó con json.dumps sobre floats, no NUMERIC de
 * Postgres. */
export async function getPortfolioReport(runBatchTag: string): Promise<PortfolioReport | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT report_json FROM portfolio_reports WHERE run_batch_tag = $1`,
    [runBatchTag]
  );
  return rows[0]?.report_json ?? null;
}
