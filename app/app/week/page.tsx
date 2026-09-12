import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPaperTradingRunBatchTag, getPaperTradingReport } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { DailyPnLChart } from "@/components/DailyPnLChart";
import { ConfidenceBucketBars } from "@/components/ConfidenceBucketBars";

export const dynamic = "force-dynamic";

const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const VERSION_LABELS: Record<string, string> = { CONSERVATIVE: "Conservative", AGGRESSIVE: "Aggressive", BALANCED: "Balanced" };

export default async function SignalsThisWeekPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/week" />
        <h1 className="text-2xl font-bold mb-4">Signals This Week</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const tag = await getLatestPaperTradingRunBatchTag();
  if (!tag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/week" />
        <h1 className="text-2xl font-bold mb-4">Signals This Week</h1>
        <p className="text-sm text-neutral-500">
          Todavía no hay ningún reporte de paper trading. Corre{" "}
          <code>python -m pipeline.paper_trading.report</code> (ver RUNBOOK.md §3.10).
        </p>
      </main>
    );
  }

  const report = await getPaperTradingReport(tag);
  if (!report) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/week" />
        <h1 className="text-2xl font-bold mb-4">Signals This Week</h1>
        <p className="text-sm text-neutral-500">No se pudo leer el reporte para <code>{tag}</code>.</p>
      </main>
    );
  }

  const anyVersion = report.versions.BALANCED ?? Object.values(report.versions)[0];
  const eventsDetected = anyVersion?.predictions.length ?? 0;

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/week" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Signals This Week</h1>
        <p className="text-sm text-neutral-500 mt-1">
          Semana simulada: <code className="font-mono">{report.week_start}</code> a{" "}
          <code className="font-mono">{report.week_end}</code> · Eventos detectados: {eventsDetected} · Corrida:{" "}
          <code className="font-mono">{tag}</code>
        </p>
      </header>

      <div className="flex flex-col md:flex-row gap-4">
        {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => {
          const v = report.versions[version];
          return (
            <div key={version} className="flex-1 min-w-[300px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
              <h2 className="text-lg font-semibold mb-3">{VERSION_LABELS[version]}</h2>

              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm mb-3">
                <dt className="text-neutral-500">Trades ejecutados</dt>
                <dd className="text-right font-mono">{v.n_open_positions + v.n_closed_trades}</dd>

                <dt className="text-neutral-500">Posiciones abiertas</dt>
                <dd className="text-right font-mono">{v.n_open_positions}</dd>

                <dt className="text-neutral-500">Cerradas esta semana</dt>
                <dd className="text-right font-mono">{v.n_closed_trades}</dd>

                <dt className="text-neutral-500">Win rate semana</dt>
                <dd className="text-right font-mono">
                  {v.trade_metrics.win_rate !== null ? `${(v.trade_metrics.win_rate * 100).toFixed(0)}%` : "—"}
                </dd>
              </dl>

              {v.alerts.length > 0 && (
                <div className="mb-3 space-y-1">
                  {v.alerts.map((a, i) => (
                    <p
                      key={i}
                      className={`text-xs rounded px-2 py-1 ${
                        a.type === "WARNING"
                          ? "bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400"
                          : "bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-400"
                      }`}
                    >
                      {a.type === "WARNING" ? "⚠" : "🎉"} {a.message}
                    </p>
                  ))}
                </div>
              )}

              <p className="text-xs text-neutral-500 mb-1">P&L diario (trades cerrados)</p>
              <DailyPnLChart trades={v.last_10_closed_trades} />

              <p className="text-xs text-neutral-500 mt-3 mb-1">
                Calibración preliminar (n={v.calibration.n}
                {v.calibration.correlation !== null && `, correl=${v.calibration.correlation.toFixed(2)}`})
              </p>
              <ConfidenceBucketBars buckets={v.calibration.buckets} />

              <p className="text-xs text-neutral-500 mt-3">
                {v.comparison_with_historical_backtest.available && v.comparison_with_historical_backtest.comparable
                  ? `¿Coincide con el backtest histórico? ${v.comparison_with_historical_backtest.matches_historical ? "Sí" : "No"} (win rate semana ${((v.comparison_with_historical_backtest.week_win_rate ?? 0) * 100).toFixed(0)}% vs histórico ${((v.comparison_with_historical_backtest.historical_win_rate ?? 0) * 100).toFixed(0)}%)`
                  : v.comparison_with_historical_backtest.note ?? "Sin backtest histórico con qué comparar todavía."}
              </p>
            </div>
          );
        })}
      </div>
    </main>
  );
}
