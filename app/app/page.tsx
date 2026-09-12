import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPaperTradingRunBatchTag, getLatestPortfolioRunBatchTag, getPaperTradingReport, getPortfolioReport } from "@/lib/queries";
import { VersionOverviewCard } from "@/components/VersionOverviewCard";
import { Nav } from "@/components/Nav";

export const dynamic = "force-dynamic"; // siempre lee datos frescos de Postgres, sin caché estática

const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;

export default async function OverviewPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Money POC — Dashboard</h1>
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

  const portfolioTag = await getLatestPortfolioRunBatchTag();
  if (!portfolioTag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Money POC — Dashboard</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no hay ningún backtest de cartera registrado.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Corre <code>python -m pipeline.backtest.portfolio_report</code> (o espera al
            pipeline nocturno) para generar el primero. Ver <code>RUNBOOK.md §3.9</code>.
          </p>
        </div>
      </main>
    );
  }

  const [portfolioReport, paperTag] = await Promise.all([getPortfolioReport(portfolioTag), getLatestPaperTradingRunBatchTag()]);
  const paperReport = paperTag ? await getPaperTradingReport(paperTag) : null;

  if (!portfolioReport) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Money POC — Dashboard</h1>
        <p className="text-sm text-neutral-500">No se pudo leer el reporte para <code>{portfolioTag}</code>.</p>
      </main>
    );
  }

  const findingByVersion = new Map(portfolioReport.recommendation.findings.map((f) => [f.split(":")[0], f]));

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Money POC — Dashboard</h1>
        <p className="text-sm text-neutral-500 mt-1">
          Hoy: <code className="font-mono">{new Date().toISOString().slice(0, 10)}</code> · Capital inicial:{" "}
          <code className="font-mono">${portfolioReport.starting_capital.toLocaleString("en-US")}</code> por versión ·
          Backtest: <code className="font-mono">{portfolioTag}</code>
          {paperTag && (
            <>
              {" "}
              · Paper trading: <code className="font-mono">{paperTag}</code>
            </>
          )}
        </p>
        {!paperReport && (
          <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
            Sin reporte de paper trading todavía — corre <code>python -m pipeline.paper_trading.report</code> (ver
            RUNBOOK.md §3.10) para ver el overlay de la semana en vivo.
          </p>
        )}
        <p className="text-xs text-neutral-400 mt-1">
          Recuerda: el número que responde &quot;¿existe un edge?&quot; es el event study (n en
          miles), no este backtest (n en cientos) ni el paper trading (n en unidades). Este
          dashboard responde &quot;¿es operable?&quot;, no &quot;¿es significativo?&quot;.
        </p>
      </header>

      <div
        className={`border rounded-lg p-4 mb-6 text-sm ${
          portfolioReport.recommendation.verdict.startsWith("SÍ")
            ? "border-green-400 bg-green-50 dark:bg-green-950 dark:border-green-800"
            : "border-neutral-300 dark:border-neutral-700"
        }`}
      >
        <span className="font-semibold">¿Invertir dinero real? {portfolioReport.recommendation.verdict}</span>
      </div>

      <div className="flex flex-col md:flex-row gap-4">
        {VERSION_ORDER.filter((v) => portfolioReport.versions[v]).map((version) => (
          <VersionOverviewCard
            key={version}
            version={version}
            portfolio={portfolioReport.versions[version]}
            paper={paperReport?.versions[version]}
            startingCapital={portfolioReport.starting_capital}
            recommendationFinding={findingByVersion.get(version)}
          />
        ))}
      </div>
    </main>
  );
}
