import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport, getLatestPaperTradingRunBatchTag, getPaperTradingReport } from "@/lib/queries";
import { PortfolioVersionCard } from "@/components/PortfolioVersionCard";
import { Nav } from "@/components/Nav";
import { ExportPdfButton } from "@/components/ExportPdfButton";
import { DailyPnLChart } from "@/components/DailyPnLChart";
import { ConfidenceBucketBars } from "@/components/ConfidenceBucketBars";

export const dynamic = "force-dynamic";

// cartera/page.tsx — fusiona lo que antes eran dos pestañas separadas
// ("Backtest Analysis" y "Signals This Week"): ambas responden la misma
// pregunta de fondo ("¿cómo le va a la cartera?"), solo que una mira el
// histórico completo y la otra la semana en curso — tiene más sentido
// como dos secciones de una misma pantalla que como dos pestañas sueltas.
const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const VERSION_LABELS: Record<string, string> = { CONSERVATIVE: "Conservador", AGGRESSIVE: "Agresivo", BALANCED: "Equilibrado" };

export default async function CarteraPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/cartera" />
        <h1 className="text-2xl font-bold mb-4">Cartera</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const [portfolioTag, paperTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestPaperTradingRunBatchTag()]);

  if (!portfolioTag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/cartera" />
        <h1 className="text-2xl font-bold mb-4">Cartera</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no hay ningún backtest de cartera registrado.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Espera al pipeline nocturno para generar el primero.</p>
        </div>
      </main>
    );
  }

  const [report, paperReport] = await Promise.all([getPortfolioReport(portfolioTag), paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null)]);

  if (!report) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/cartera" />
        <h1 className="text-2xl font-bold mb-4">Cartera</h1>
        <p className="text-sm text-neutral-500">
          No se pudo leer el reporte para <code>{portfolioTag}</code>.
        </p>
      </main>
    );
  }

  const { recommendation, bias_report } = report;
  const verdictIsYes = recommendation.verdict.startsWith("SÍ");

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/cartera" />
      <header className="mb-6">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <h1 className="text-2xl font-bold">Cartera</h1>
          <ExportPdfButton report={report} />
        </div>
        <p className="text-sm text-neutral-500 mt-1">
          Cómo le ha ido al sistema si se hubiera operado — todo simulado, nunca con dinero real.
        </p>
      </header>

      {/* Sección 1: histórico */}
      <section className="mb-10">
        <h2 className="text-lg font-semibold mb-3">Resultado histórico completo</h2>

        <div
          className={`border rounded-lg p-4 mb-4 ${
            verdictIsYes ? "border-green-400 bg-green-50 dark:bg-green-950 dark:border-green-800" : "border-neutral-300 dark:border-neutral-700"
          }`}
        >
          <p className="font-semibold mb-2">¿Invertir dinero real? {recommendation.verdict}</p>
          <ul className="text-sm list-disc list-inside space-y-0.5">
            {recommendation.findings.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>

        <div className="border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 mb-4 text-sm">
          <p className="font-medium mb-1">Sesgos de datos a tener en cuenta</p>
          <p className="text-neutral-500">
            {bias_report.n_delisted}/{bias_report.n_total_tickers} acciones que desaparecieron de bolsa (
            {bias_report.survivorship_bias_pct?.toFixed(1) ?? "—"}% posible sesgo) · {bias_report.n_price_gaps}/{bias_report.n_price_rows}{" "}
            filas de precio con huecos ({bias_report.data_gap_pct?.toFixed(1) ?? "—"}%)
          </p>
        </div>

        <p className="text-xs text-neutral-500 mb-3">
          Corrida: <code className="font-mono">{portfolioTag}</code> · Capital inicial:{" "}
          <code className="font-mono">${report.starting_capital.toLocaleString("en-US")}</code>
        </p>

        <div className="flex flex-col md:flex-row gap-4">
          {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => (
            <PortfolioVersionCard key={version} report={report.versions[version]} startingCapital={report.starting_capital} />
          ))}
        </div>
      </section>

      {/* Sección 2: esta semana */}
      <section>
        <h2 className="text-lg font-semibold mb-3">Esta semana (simulación en papel)</h2>
        {!paperReport ? (
          <p className="text-sm text-neutral-500 italic">Todavía no hay ningún reporte de esta semana.</p>
        ) : (
          <>
            <p className="text-xs text-neutral-500 mb-3">
              Semana: <code className="font-mono">{paperReport.week_start}</code> a <code className="font-mono">{paperReport.week_end}</code> —
              datos reales, sin dinero real.
            </p>
            <div className="flex flex-col md:flex-row gap-4">
              {VERSION_ORDER.filter((v) => paperReport.versions[v]).map((version) => {
                const v = paperReport.versions[version];
                return (
                  <div key={version} className="flex-1 min-w-[300px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
                    <h3 className="font-semibold mb-3">{VERSION_LABELS[version]}</h3>

                    <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm mb-3">
                      <dt className="text-neutral-500">Operaciones esta semana</dt>
                      <dd className="text-right font-mono">{v.n_open_positions + v.n_closed_trades}</dd>
                      <dt className="text-neutral-500">Abiertas ahora</dt>
                      <dd className="text-right font-mono">{v.n_open_positions}</dd>
                      <dt className="text-neutral-500">Cerradas</dt>
                      <dd className="text-right font-mono">{v.n_closed_trades}</dd>
                      <dt className="text-neutral-500">Acierto esta semana</dt>
                      <dd className="text-right font-mono">{v.trade_metrics.win_rate !== null ? `${(v.trade_metrics.win_rate * 100).toFixed(0)}%` : "—"}</dd>
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

                    <p className="text-xs text-neutral-500 mb-1">Resultado día a día</p>
                    <DailyPnLChart trades={v.last_10_closed_trades} />

                    <p className="text-xs text-neutral-500 mt-3 mb-1">¿El sistema sabe cuándo está seguro? (n={v.calibration.n})</p>
                    <ConfidenceBucketBars buckets={v.calibration.buckets} />

                    <p className="text-xs text-neutral-500 mt-3">
                      {v.comparison_with_historical_backtest.available && v.comparison_with_historical_backtest.comparable
                        ? `¿Coincide con el histórico? ${v.comparison_with_historical_backtest.matches_historical ? "Sí" : "No"} (esta semana ${((v.comparison_with_historical_backtest.week_win_rate ?? 0) * 100).toFixed(0)}% vs histórico ${((v.comparison_with_historical_backtest.historical_win_rate ?? 0) * 100).toFixed(0)}%)`
                        : v.comparison_with_historical_backtest.note ?? "Sin histórico con qué comparar todavía."}
                    </p>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
