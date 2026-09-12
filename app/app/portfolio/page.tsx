import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport } from "@/lib/queries";
import { PortfolioVersionCard } from "@/components/PortfolioVersionCard";

export const dynamic = "force-dynamic"; // siempre lee datos frescos de Postgres, sin caché estática

const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;

export default async function PortfolioBacktestPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Backtest de cartera — Fase 4</h1>
        <div className="border border-amber-300 bg-amber-50 dark:bg-amber-950 dark:border-amber-800 rounded-lg p-4">
          <p className="font-medium mb-2">DATABASE_URL no está configurada.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Ver <code>RUNBOOK.md</code> para provisionar una base gratuita y configurar la
            variable de entorno.
          </p>
        </div>
      </main>
    );
  }

  const runBatchTag = await getLatestPortfolioRunBatchTag();

  if (!runBatchTag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Backtest de cartera — Fase 4</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no hay ningún backtest de cartera registrado.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Corre <code>python -m pipeline.backtest.portfolio_report</code> (o espera al
            pipeline nocturno de GitHub Actions) para generar el primero. Ver{" "}
            <code>RUNBOOK.md §3.9</code>.
          </p>
        </div>
        <Link href="/" className="text-sm underline mt-4 inline-block">
          ← Volver al backtest simple
        </Link>
      </main>
    );
  }

  const report = await getPortfolioReport(runBatchTag);

  if (!report) {
    // No debería pasar (runBatchTag viene de la misma tabla), pero
    // report_json podría faltar si alguien borró la fila entre las dos
    // queries — mejor un mensaje claro que un 500 sin contexto.
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Backtest de cartera — Fase 4</h1>
        <p className="text-sm text-neutral-500">
          No se pudo leer el reporte para <code>{runBatchTag}</code>.
        </p>
      </main>
    );
  }

  const { recommendation, bias_report } = report;
  const verdictIsYes = recommendation.verdict.startsWith("SÍ");

  return (
    <main className="max-w-7xl mx-auto p-6">
      <header className="mb-6">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <h1 className="text-2xl font-bold">Backtest de cartera — Fase 4</h1>
          <Link href="/" className="text-sm underline">
            ← Backtest simple (Fase 1)
          </Link>
        </div>
        <p className="text-sm text-neutral-500 mt-1">
          Corrida: <code className="font-mono">{runBatchTag}</code> · Capital inicial:{" "}
          <code className="font-mono">${report.starting_capital.toLocaleString("en-US")}</code> · Solo
          lectura — generado por <code>pipeline/backtest/portfolio_report.py</code>
        </p>
      </header>

      <div
        className={`border rounded-lg p-4 mb-6 ${
          verdictIsYes
            ? "border-green-400 bg-green-50 dark:bg-green-950 dark:border-green-800"
            : "border-neutral-300 dark:border-neutral-700"
        }`}
      >
        <p className="font-semibold mb-2">¿Invertir dinero real? {recommendation.verdict}</p>
        <ul className="text-sm list-disc list-inside space-y-0.5">
          {recommendation.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      </div>

      <div className="border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 mb-6 text-sm">
        <p className="font-medium mb-1">Reporte de sesgos (sobre todo el universo, no por versión)</p>
        <p className="text-neutral-500">
          {bias_report.n_delisted}/{bias_report.n_total_tickers} tickers deslistados (
          {bias_report.survivorship_bias_pct?.toFixed(1) ?? "—"}% posible sesgo de supervivencia) ·{" "}
          {bias_report.n_price_gaps} filas de precio con WARNING de gap de{" "}
          {bias_report.n_price_rows} totales ({bias_report.data_gap_pct?.toFixed(1) ?? "—"}%)
        </p>
      </div>

      <div className="flex flex-col md:flex-row gap-4">
        {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => (
          <PortfolioVersionCard key={version} report={report.versions[version]} startingCapital={report.starting_capital} />
        ))}
      </div>
    </main>
  );
}
