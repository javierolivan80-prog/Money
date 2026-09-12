import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import { getEquityCurve, getLatestRunBatchTag, getStrategySummaries, getTrades } from "@/lib/queries";
import { StrategyColumn } from "@/components/StrategyColumn";

export const dynamic = "force-dynamic"; // siempre lee datos frescos de Postgres, sin caché estática

export default async function DashboardPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Money POC — Dashboard</h1>
        <div className="border border-amber-300 bg-amber-50 dark:bg-amber-950 dark:border-amber-800 rounded-lg p-4">
          <p className="font-medium mb-2">DATABASE_URL no está configurada.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Este dashboard es de solo lectura sobre el Postgres que llena el pipeline
            de <code>.github/workflows/nightly_pipeline.yml</code>. Ver{" "}
            <code>RUNBOOK.md</code> para provisionar una base gratuita (Neon/Supabase)
            y configurar la variable de entorno, tanto para el pipeline como para este
            dashboard (en Vercel: Project Settings → Environment Variables).
          </p>
        </div>
      </main>
    );
  }

  const runBatchTag = await getLatestRunBatchTag();

  if (!runBatchTag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <h1 className="text-2xl font-bold mb-4">Money POC — Dashboard</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no hay ningún backtest_run registrado.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            El pipeline nocturno (GitHub Actions) aún no ha corrido, o corrió pero no
            generó trades. Revisa la pestaña Actions del repo, y{" "}
            <code>ARCHITECTURE_LEAN.md §9</code> para el plan de 7 días.
          </p>
        </div>
        <Link href="/portfolio" className="text-sm underline mt-4 inline-block">
          Backtest de cartera (Fase 4) →
        </Link>
      </main>
    );
  }

  const [summaries] = await Promise.all([getStrategySummaries(runBatchTag)]);
  const strategyData = await Promise.all(
    summaries.map(async (summary) => ({
      summary,
      equity: await getEquityCurve(summary.strategy_version, runBatchTag),
      trades: await getTrades(summary.strategy_version, runBatchTag),
    }))
  );

  return (
    <main className="max-w-7xl mx-auto p-6">
      <header className="mb-6">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <h1 className="text-2xl font-bold">Money POC — Dashboard</h1>
          <Link href="/portfolio" className="text-sm underline">
            Backtest de cartera (Fase 4) →
          </Link>
        </div>
        <p className="text-sm text-neutral-500 mt-1">
          Corrida: <code className="font-mono">{runBatchTag}</code> · Solo lectura ·
          Datos generados por el pipeline nocturno (ver <code>RUNBOOK.md</code>)
        </p>
        <p className="text-xs text-neutral-400 mt-1">
          Recuerda: el número que responde &quot;¿existe un edge?&quot; es el event
          study (n en miles, ver AUDIT_LEAN.md §2.2.3), no este backtest (n en
          cientos). Este dashboard responde &quot;¿es operable?&quot;, no
          &quot;¿es significativo?&quot;.
        </p>
      </header>

      <div className="flex flex-col md:flex-row gap-4">
        {strategyData.map(({ summary, equity, trades }) => (
          <StrategyColumn key={summary.strategy_version} summary={summary} equity={equity} trades={trades} />
        ))}
      </div>
    </main>
  );
}
