// PortfolioVersionCard.tsx — una columna por versión (Conservative/
// Aggressive/Balanced) del reporte de backtest_report.py. Renderiza el JSON
// ya calculado por Python (portfolio_metrics.py) tal cual llega — este
// componente no recalcula ni una fórmula, solo formatea unidades (ver los
// comentarios de unidad en cada fmt* de abajo, porque el JSON mezcla
// fracciones 0-1 con puntos porcentuales ya multiplicados por 100, fiel a
// como cada función de portfolio_metrics.py los devuelve).
import type { PortfolioVersionReport } from "@/lib/queries";
import { PortfolioEquityCurve } from "./PortfolioEquityCurve";

const VERSION_LABELS: Record<string, string> = {
  CONSERVATIVE: "Conservative",
  AGGRESSIVE: "Aggressive",
  BALANCED: "Balanced",
};

function fmtFraction(v: number | null, digits = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function fmtPctPoints(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)}%`;
}

function fmtRatio(v: number | null, digits = 2): string {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

function fmtDollars(v: number | null): string {
  return v === null || v === undefined
    ? "—"
    : v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

export function PortfolioVersionCard({
  report,
  startingCapital,
}: {
  report: PortfolioVersionReport;
  startingCapital: number;
}) {
  const { trade_metrics: tm, equity_metrics: em, calibration: cal, asymmetry, no_lookahead_violations, temporal_stability } = report;
  const eventTypeRows = Object.values(report.metrics_by_event_type);

  return (
    <div className="flex-1 min-w-[300px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold mb-3">{VERSION_LABELS[report.version] ?? report.version}</h2>

      {no_lookahead_violations.length > 0 && (
        <div className="border border-red-400 bg-red-50 dark:bg-red-950 dark:border-red-800 rounded p-2 mb-3 text-xs">
          <p className="font-medium text-red-700 dark:text-red-400 mb-1">
            ⚠ {no_lookahead_violations.length} violación(es) anti-look-ahead — resultado NO fiable
          </p>
          <ul className="list-disc list-inside text-red-600 dark:text-red-300">
            {no_lookahead_violations.slice(0, 3).map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm mb-3">
        <dt className="text-neutral-500">Trades</dt>
        <dd className="text-right font-mono">{tm.total_trades}</dd>

        <dt className="text-neutral-500">Win rate</dt>
        <dd className="text-right font-mono">{fmtFraction(tm.win_rate)}</dd>

        <dt className="text-neutral-500">Profit factor</dt>
        <dd className="text-right font-mono">{fmtRatio(tm.profit_factor)}</dd>

        <dt className="text-neutral-500">Expectancy</dt>
        <dd className="text-right font-mono">{fmtPctPoints(tm.expectancy)}</dd>

        <dt className="text-neutral-500">Balance final</dt>
        <dd className="text-right font-mono">{fmtDollars(em.final_balance)}</dd>

        <dt className="text-neutral-500">Retorno total</dt>
        <dd className="text-right font-mono">{fmtFraction(em.total_return)}</dd>

        <dt className="text-neutral-500">Sharpe</dt>
        <dd className="text-right font-mono">{fmtRatio(em.sharpe_ratio)}</dd>

        <dt className="text-neutral-500">Sortino</dt>
        <dd className="text-right font-mono">{fmtRatio(em.sortino_ratio)}</dd>

        <dt className="text-neutral-500">Calmar</dt>
        <dd className="text-right font-mono">{fmtRatio(em.calmar_ratio)}</dd>

        <dt className="text-neutral-500">Max drawdown</dt>
        <dd className="text-right font-mono">{fmtFraction(em.max_drawdown)}</dd>

        <dt className="text-neutral-500">Recovery factor</dt>
        <dd className="text-right font-mono">{fmtRatio(em.recovery_factor)}</dd>

        <dt className="text-neutral-500">Rachas (G/P)</dt>
        <dd className="text-right font-mono">
          {tm.consecutive_wins}/{tm.consecutive_losses}
        </dd>

        <dt className="text-neutral-500">Calibración</dt>
        <dd className={`text-right font-mono ${cal.meets_target ? "text-green-600 dark:text-green-400" : ""}`}>
          {fmtRatio(cal.calibration_score)}
        </dd>

        <dt className="text-neutral-500">R² predicho-real</dt>
        <dd className="text-right font-mono">{fmtRatio(report.prediction_regression.r_squared)}</dd>
      </dl>

      {asymmetry.n_trades > 0 && (
        <p className="text-xs text-neutral-500 mb-3">
          Asimetría (umbral ±{asymmetry.threshold_pct}%): {fmtPctPoints(asymmetry.pct_reaching_positive_threshold, 0)}{" "}
          alcanzan +umbral vs {fmtPctPoints(asymmetry.pct_reaching_negative_threshold, 0)} -umbral
          {asymmetry.asymmetric_favoring_gains !== null &&
            (asymmetry.asymmetric_favoring_gains ? " (favorece ganancias)" : " (favorece pérdidas)")}
        </p>
      )}

      <div className="mb-4">
        <PortfolioEquityCurve points={report.equity_curve} startingCapital={startingCapital} />
      </div>

      {temporal_stability && (
        <details className="text-xs mb-3">
          <summary className="cursor-pointer text-neutral-500 mb-1">
            Estabilidad temporal (split {temporal_stability.split_date})
          </summary>
          <p className={temporal_stability.stable === false ? "text-amber-600 dark:text-amber-400" : "text-neutral-500"}>
            {temporal_stability.stable === null
              ? "Muestra insuficiente para comparar"
              : temporal_stability.stable
                ? "Estable entre periodos"
                : "Diverge entre periodos"}
          </p>
          {temporal_stability.warnings.map((w, i) => (
            <p key={i} className="text-amber-600 dark:text-amber-400">
              ⚠ {w}
            </p>
          ))}
        </details>
      )}

      {eventTypeRows.length > 0 && (
        <details className="text-xs mb-3">
          <summary className="cursor-pointer text-neutral-500 mb-2">Por tipo de evento ({eventTypeRows.length})</summary>
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-neutral-200 dark:border-neutral-800">
                <th className="py-1 pr-2">Clase</th>
                <th className="py-1 pr-2 text-right">N</th>
                <th className="py-1 pr-2 text-right">Win rate</th>
                <th className="py-1 pr-2 text-right">Retorno medio</th>
              </tr>
            </thead>
            <tbody>
              {eventTypeRows.map((row) => (
                <tr key={row.event_type} className="border-b border-neutral-100 dark:border-neutral-900">
                  <td className="py-1 pr-2">
                    {row.event_type.replace(/^8K_/, "")}
                    {row.insufficient_sample && <span title="n < 20 — muestra insuficiente"> ⚠</span>}
                  </td>
                  <td className="py-1 pr-2 text-right font-mono">{row.n_trades}</td>
                  <td className="py-1 pr-2 text-right font-mono">{fmtFraction(row.win_rate)}</td>
                  <td className="py-1 pr-2 text-right font-mono">{fmtPctPoints(row.avg_return)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-neutral-500 mb-2">
          Top 10 ganadores / perdedores
        </summary>
        <TradeMiniTable trades={report.top_10_winners} />
        <div className="mt-2" />
        <TradeMiniTable trades={report.top_10_losers} />
      </details>
    </div>
  );
}

function TradeMiniTable({ trades }: { trades: PortfolioVersionReport["top_10_winners"] }) {
  if (trades.length === 0) {
    return <p className="text-neutral-500 italic">Sin trades.</p>;
  }
  return (
    <table className="w-full text-left border-collapse">
      <thead>
        <tr className="border-b border-neutral-200 dark:border-neutral-800">
          <th className="py-1 pr-2">Ticker</th>
          <th className="py-1 pr-2">Salida</th>
          <th className="py-1 pr-2 text-right">PnL %</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={`${t.event_id}-${t.exit_date}`} className="border-b border-neutral-100 dark:border-neutral-900">
            <td className="py-1 pr-2 font-mono">{t.ticker ?? "—"}</td>
            <td className="py-1 pr-2">{t.exit_reason}</td>
            <td className={`py-1 pr-2 text-right font-mono ${t.pnl_pct >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
              {t.pnl_pct.toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
