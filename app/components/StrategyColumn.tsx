import type { EquityPoint, StrategySummary, TradeRow } from "@/lib/queries";
import { EquityCurve } from "./EquityCurve";

const STRATEGY_LABELS: Record<string, string> = {
  CONSERVATIVE: "Conservative",
  BALANCED: "Balanced",
  AGGRESSIVE: "Aggressive",
};

function fmtPct(v: number | null, digits = 2): string {
  return v === null ? "—" : `${(v * (Math.abs(v) < 1 ? 100 : 1)).toFixed(digits)}%`;
}

function fmtNum(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
}

export function StrategyColumn({
  summary,
  equity,
  trades,
}: {
  summary: StrategySummary;
  equity: EquityPoint[];
  trades: TradeRow[];
}) {
  const survivorshipCount = trades.filter((t) => t.had_survivorship_warning).length;

  return (
    <div className="flex-1 min-w-[280px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold mb-3">{STRATEGY_LABELS[summary.strategy_version]}</h2>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm mb-4">
        <dt className="text-neutral-500">Trades</dt>
        <dd className="text-right font-mono">{summary.n_trades}</dd>

        <dt className="text-neutral-500">Win rate</dt>
        <dd className="text-right font-mono">{fmtPct(summary.win_rate)}</dd>

        <dt className="text-neutral-500">Retorno medio</dt>
        <dd className="text-right font-mono">{fmtNum(summary.mean_return_pct)}%</dd>

        <dt className="text-neutral-500">Sharpe (anualizado)</dt>
        <dd className="text-right font-mono">{fmtNum(summary.sharpe_annualized)}</dd>

        <dt className="text-neutral-500">Max drawdown</dt>
        <dd className="text-right font-mono">{fmtNum(summary.max_drawdown_pct)}%</dd>

        <dt className="text-neutral-500">Calibración</dt>
        <dd className="text-right font-mono">{fmtNum(summary.calibration_corr)}</dd>
      </dl>

      {survivorshipCount > 0 && (
        <p className="text-xs text-amber-600 dark:text-amber-400 mb-3">
          ⚠ {survivorshipCount} de {trades.length} trades con WARNING de posible gap de
          supervivencia (AUDIT_LEAN.md §2.4) — interpretar con cautela.
        </p>
      )}

      <div className="mb-4">
        <EquityCurve points={equity} />
      </div>

      <details className="text-xs">
        <summary className="cursor-pointer text-neutral-500 mb-2">
          Detalle de trades ({trades.length})
        </summary>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-neutral-200 dark:border-neutral-800">
                <th className="py-1 pr-2">Ticker</th>
                <th className="py-1 pr-2">Clase</th>
                <th className="py-1 pr-2">Entrada</th>
                <th className="py-1 pr-2">Dir</th>
                <th className="py-1 pr-2 text-right">Pred %</th>
                <th className="py-1 pr-2 text-right">Real %</th>
                <th className="py-1">Hit</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.event_id} className="border-b border-neutral-100 dark:border-neutral-900">
                  <td className="py-1 pr-2 font-mono">
                    {t.ticker}
                    {t.had_survivorship_warning && <span title="Posible gap de supervivencia"> ⚠</span>}
                  </td>
                  <td className="py-1 pr-2">{t.event_class.replace(/^8K_/, "")}</td>
                  <td className="py-1 pr-2 font-mono">{t.entry_date}</td>
                  <td className="py-1 pr-2">{t.predicted_direction}</td>
                  <td className="py-1 pr-2 text-right font-mono">{t.predicted_ev_pct.toFixed(2)}</td>
                  <td className="py-1 pr-2 text-right font-mono">
                    {t.realized_return_20d_pct !== null ? t.realized_return_20d_pct.toFixed(2) : "—"}
                  </td>
                  <td className="py-1">{t.hit_20d === null ? "—" : t.hit_20d ? "✓" : "✗"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
