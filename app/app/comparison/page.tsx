import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { ComparisonTable, type ComparisonRow } from "@/components/ComparisonTable";

export const dynamic = "force-dynamic";

const RECOMMENDED_FOR: Record<string, string> = {
  conservative: "Risk-averse",
  aggressive: "Risk-taker",
  balanced: "Balanced",
};

export default async function ComparisonPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/comparison" />
        <h1 className="text-2xl font-bold mb-4">Comparison</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const tag = await getLatestPortfolioRunBatchTag();
  if (!tag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/comparison" />
        <h1 className="text-2xl font-bold mb-4">Comparison</h1>
        <p className="text-sm text-neutral-500">Todavía no hay ningún backtest de cartera registrado.</p>
      </main>
    );
  }

  const report = await getPortfolioReport(tag);
  if (!report) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/comparison" />
        <h1 className="text-2xl font-bold mb-4">Comparison</h1>
        <p className="text-sm text-neutral-500">No se pudo leer el reporte para <code>{tag}</code>.</p>
      </main>
    );
  }

  const v = report.versions;
  const fmtPct = (x: number | null) => (x === null ? "—" : `${(x * 100).toFixed(1)}%`);
  const fmtRatio = (x: number | null) => (x === null ? "—" : x.toFixed(2));
  const tradesPerYear = (version: "CONSERVATIVE" | "AGGRESSIVE" | "BALANCED") => {
    const r = v[version];
    const nYears = r.equity_curve.length > 0 ? r.equity_curve.length / 252 : null;
    return nYears && nYears > 0 ? (r.trade_metrics.total_trades / nYears).toFixed(0) : "—";
  };

  const rows: ComparisonRow[] = [
    {
      metric: "Total return",
      conservative: fmtPct(v.CONSERVATIVE.equity_metrics.total_return),
      aggressive: fmtPct(v.AGGRESSIVE.equity_metrics.total_return),
      balanced: fmtPct(v.BALANCED.equity_metrics.total_return),
    },
    {
      metric: "Sharpe",
      conservative: fmtRatio(v.CONSERVATIVE.equity_metrics.sharpe_ratio),
      aggressive: fmtRatio(v.AGGRESSIVE.equity_metrics.sharpe_ratio),
      balanced: fmtRatio(v.BALANCED.equity_metrics.sharpe_ratio),
    },
    {
      metric: "Win rate",
      conservative: fmtPct(v.CONSERVATIVE.trade_metrics.win_rate),
      aggressive: fmtPct(v.AGGRESSIVE.trade_metrics.win_rate),
      balanced: fmtPct(v.BALANCED.trade_metrics.win_rate),
    },
    {
      metric: "Max drawdown",
      conservative: fmtPct(v.CONSERVATIVE.equity_metrics.max_drawdown),
      aggressive: fmtPct(v.AGGRESSIVE.equity_metrics.max_drawdown),
      balanced: fmtPct(v.BALANCED.equity_metrics.max_drawdown),
    },
    {
      metric: "Trade frequency (trades/año)",
      conservative: tradesPerYear("CONSERVATIVE"),
      aggressive: tradesPerYear("AGGRESSIVE"),
      balanced: tradesPerYear("BALANCED"),
    },
    {
      metric: "Recommended for",
      conservative: RECOMMENDED_FOR.conservative,
      aggressive: RECOMMENDED_FOR.aggressive,
      balanced: RECOMMENDED_FOR.balanced,
    },
  ];

  return (
    <main className="max-w-4xl mx-auto p-6">
      <Nav active="/comparison" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Comparison</h1>
        <p className="text-sm text-neutral-500 mt-1">
          Corrida: <code className="font-mono">{tag}</code>
        </p>
      </header>
      <ComparisonTable rows={rows} />
    </main>
  );
}
