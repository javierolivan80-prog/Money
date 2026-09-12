import type { PaperVersionReport, PortfolioVersionReport } from "@/lib/queries";
import { CombinedEquityChart } from "./CombinedEquityChart";

const VERSION_LABELS: Record<string, string> = {
  CONSERVATIVE: "Conservative",
  AGGRESSIVE: "Aggressive",
  BALANCED: "Balanced",
};

// "Recomendación: Esta versión es para..." (spec) — no hay una regla de
// negocio detrás de esto más allá de la propia forma de cada estrategia
// (portfolio_strategies.py); son las mismas 3 frases fijas del propio
// diseño de la Fase 3, no algo derivado de los datos de esta corrida.
const VERSION_BLURBS: Record<string, string> = {
  CONSERVATIVE: "perfil de riesgo bajo — tamaños de posición pequeños (2-5%), toma beneficios rápido (+2%), corta pérdidas rápido (-1.5%).",
  AGGRESSIVE: "perfil de riesgo alto — posiciones más grandes (10-20%), deja correr ganadores con trailing stop, tolera más drawdown.",
  BALANCED: "mezcla de ambos estilos según confianza/EV de cada señal — punto medio de riesgo y frecuencia de trades.",
};

function fmtFraction(v: number | null, digits = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}
function fmtRatio(v: number | null, digits = 2): string {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}
function fmtPctPoints(v: number | null, digits = 2): string {
  return v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function VersionOverviewCard({
  version,
  portfolio,
  paper,
  startingCapital,
  recommendationFinding,
}: {
  version: string;
  portfolio: PortfolioVersionReport;
  paper: PaperVersionReport | undefined;
  startingCapital: number;
  recommendationFinding: string | undefined;
}) {
  const { trade_metrics: tm, equity_metrics: em } = portfolio;
  const nYears = portfolio.equity_curve.length > 0 ? portfolio.equity_curve.length / 252 : null;
  const tradesPerYear = nYears && nYears > 0 ? tm.total_trades / nYears : null;

  return (
    <div className="flex-1 min-w-[300px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
      <h2 className="text-lg font-semibold mb-1">{VERSION_LABELS[version] ?? version}</h2>
      <p className="text-xs text-neutral-500 mb-3">{VERSION_BLURBS[version]}</p>

      <CombinedEquityChart
        equityCurve={portfolio.equity_curve}
        startingCapital={startingCapital}
        paperClosedTrades={paper?.last_10_closed_trades ?? []}
      />
      <p className="text-[10px] text-neutral-400 mt-1 mb-3">
        <span className="text-blue-600">■</span> Backtest histórico &nbsp;
        <span className="text-amber-500">■</span> Paper trading (semana {paper?.n_closed_trades ?? 0} cerradas, {paper?.n_open_positions ?? 0} abiertas)
      </p>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm mb-3">
        <dt className="text-neutral-500" title="Retorno total del backtest histórico completo">Total return</dt>
        <dd className="text-right font-mono">{fmtFraction(em.total_return)}</dd>

        <dt className="text-neutral-500" title="Retorno ajustado por riesgo, anualizado sobre la curva de equity diaria">Sharpe</dt>
        <dd className="text-right font-mono">{fmtRatio(em.sharpe_ratio)}</dd>

        <dt className="text-neutral-500" title="Peor caída desde un máximo histórico de la curva de equity">Max drawdown</dt>
        <dd className="text-right font-mono">{fmtFraction(em.max_drawdown)}</dd>

        <dt className="text-neutral-500" title="% de trades cerrados con pnl_pct > 0">Win rate</dt>
        <dd className="text-right font-mono">{fmtFraction(tm.win_rate)}</dd>

        <dt className="text-neutral-500" title="total_trades / (días de equity / 252) — aproximado">Trades/año</dt>
        <dd className="text-right font-mono">{tradesPerYear !== null ? tradesPerYear.toFixed(0) : "—"}</dd>

        <dt className="text-neutral-500" title="Suma de pnl_pct de los trades de paper trading cerrados esta semana">P&L semana (paper)</dt>
        <dd className={`text-right font-mono ${(paper?.cumulative_pnl_pct_week ?? 0) >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
          {paper ? fmtPctPoints(paper.cumulative_pnl_pct_week) : "—"}
        </dd>
      </dl>

      <details className="text-xs mb-3" open>
        <summary className="cursor-pointer text-neutral-500 mb-2">Posiciones abiertas (paper trading) — {paper?.open_positions.length ?? 0}</summary>
        {!paper || paper.open_positions.length === 0 ? (
          <p className="text-neutral-500 italic">Ninguna.</p>
        ) : (
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-neutral-200 dark:border-neutral-800">
                <th className="py-1 pr-2">Ticker</th>
                <th className="py-1 pr-2">Evento</th>
                <th className="py-1 pr-2 text-right">Entrada</th>
                <th className="py-1 pr-2 text-right">Actual</th>
                <th className="py-1 text-right">P&L no realiz.</th>
              </tr>
            </thead>
            <tbody>
              {paper.open_positions.map((p) => (
                <tr key={p.event_id} className="border-b border-neutral-100 dark:border-neutral-900">
                  <td className="py-1 pr-2 font-mono">{p.ticker}</td>
                  <td className="py-1 pr-2">{p.event_class?.replace(/^8K_/, "") ?? "—"}</td>
                  <td className="py-1 pr-2 text-right font-mono">{p.entry_price.toFixed(2)}</td>
                  <td className="py-1 pr-2 text-right font-mono">{p.current_price?.toFixed(2) ?? "—"}</td>
                  <td className={`py-1 text-right font-mono ${(p.unrealized_pnl_pct ?? 0) >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                    {p.unrealized_pnl_pct !== null ? fmtPctPoints(p.unrealized_pnl_pct) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>

      <details className="text-xs mb-3">
        <summary className="cursor-pointer text-neutral-500 mb-2">Últimas 5 cerradas (paper trading)</summary>
        {!paper || paper.last_10_closed_trades.length === 0 ? (
          <p className="text-neutral-500 italic">Ninguna todavía.</p>
        ) : (
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-neutral-200 dark:border-neutral-800">
                <th className="py-1 pr-2">Ticker</th>
                <th className="py-1 pr-2">Salida</th>
                <th className="py-1 text-right">P&L</th>
              </tr>
            </thead>
            <tbody>
              {paper.last_10_closed_trades.slice(0, 5).map((t) => (
                <tr key={`${t.event_id}-${t.exit_date}`} className="border-b border-neutral-100 dark:border-neutral-900">
                  <td className="py-1 pr-2 font-mono">{t.ticker}</td>
                  <td className="py-1 pr-2">{t.exit_reason}</td>
                  <td className={`py-1 text-right font-mono ${t.pnl_pct >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                    {fmtPctPoints(t.pnl_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>

      {recommendationFinding && (
        <p className="text-xs border-t border-neutral-200 dark:border-neutral-800 pt-2 text-neutral-500">{recommendationFinding}</p>
      )}
    </div>
  );
}
