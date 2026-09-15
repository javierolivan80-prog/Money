import Link from "next/link";
import { isDatabaseConfigured } from "@/lib/db";
import {
  getLatestPortfolioRunBatchTag,
  getPortfolioReport,
  getLatestPaperTradingRunBatchTag,
  getPaperTradingReport,
  getLatestValidationRunBatchTag,
  getValidationReport,
} from "@/lib/queries";
import { Nav } from "@/components/Nav";

export const dynamic = "force-dynamic";

// page.tsx (Inicio) — reescrita como pantalla "semáforo": la pregunta que
// alguien sin conocer el proyecto se hace al abrir esto es "¿funciona esto
// o no?", no "dame los 40 números del backtest". Esa respuesta ya la
// calcula pipeline/validation/decision.py (GREENLIGHT/YELLOWLIGHT/REDLIGHT)
// — aquí solo se traduce a lenguaje llano y se pone delante de todo lo
// demás. El detalle completo sigue disponible en /funciona y /cartera para
// quien quiera profundizar.

const VERDICT_COPY: Record<string, { emoji: string; title: string; color: string }> = {
  A: { emoji: "✅", title: "El sistema funciona bien en las pruebas", color: "border-green-400 bg-green-50 dark:bg-green-950 dark:border-green-800" },
  B: { emoji: "⚠️", title: "Funciona, pero todavía con reservas", color: "border-amber-400 bg-amber-50 dark:bg-amber-950 dark:border-amber-800" },
  C: { emoji: "❌", title: "Todavía no funciona de forma fiable", color: "border-red-400 bg-red-50 dark:bg-red-950 dark:border-red-800" },
};

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 flex-1 min-w-[160px]">
      <p className="text-xs text-neutral-500 mb-1">{label}</p>
      <p className="text-2xl font-bold">{value}</p>
      {hint && <p className="text-xs text-neutral-400 mt-1">{hint}</p>}
    </div>
  );
}

export default async function InicioPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/" />
        <h1 className="text-2xl font-bold mb-4">Money — Panel</h1>
        <div className="border border-amber-300 bg-amber-50 dark:bg-amber-950 dark:border-amber-800 rounded-lg p-4">
          <p className="font-medium mb-2">DATABASE_URL no está configurada.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Ver <code>RUNBOOK.md</code> para provisionar una base gratuita y configurar la variable de entorno.
          </p>
        </div>
      </main>
    );
  }

  const [portfolioTag, paperTag, validationTag] = await Promise.all([
    getLatestPortfolioRunBatchTag(),
    getLatestPaperTradingRunBatchTag(),
    getLatestValidationRunBatchTag(),
  ]);

  if (!portfolioTag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/" />
        <h1 className="text-2xl font-bold mb-4">Money — Panel</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no hay ningún resultado calculado.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            El pipeline nocturno todavía no ha corrido, o acaba de empezar a recoger datos. Vuelve en unas horas.
          </p>
        </div>
      </main>
    );
  }

  const [portfolioReport, paperReport, validationReport] = await Promise.all([
    getPortfolioReport(portfolioTag),
    paperTag ? getPaperTradingReport(paperTag) : Promise.resolve(null),
    validationTag ? getValidationReport(validationTag) : Promise.resolve(null),
  ]);

  const balanced = portfolioReport?.versions.BALANCED;
  const verdict = validationReport ? VERDICT_COPY[validationReport.best_decision.option] : null;

  const openPositions = paperReport ? Object.values(paperReport.versions).reduce((sum, v) => sum + v.n_open_positions, 0) : 0;

  return (
    <main className="max-w-5xl mx-auto p-6">
      <Nav active="/" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Money — Panel</h1>
        <p className="text-sm text-neutral-500 mt-1">Resumen de un vistazo. Todo lo de aquí tiene el detalle completo en las otras pestañas.</p>
      </header>

      {/* Semáforo */}
      {verdict ? (
        <section className={`border-2 rounded-lg p-5 mb-6 ${verdict.color}`}>
          <p className="text-3xl mb-1">{verdict.emoji}</p>
          <p className="text-xl font-bold mb-2">{verdict.title}</p>
          <p className="text-sm">{validationReport!.best_decision.recommendation}</p>
          <Link href="/funciona" className="text-sm underline mt-2 inline-block">
            Ver por qué →
          </Link>
        </section>
      ) : (
        <section className="border-2 border-neutral-300 dark:border-neutral-700 rounded-lg p-5 mb-6">
          <p className="text-xl font-bold mb-2">🕐 Todavía acumulando datos</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Hacen falta más días de eventos reales antes de poder decir con confianza si el sistema funciona. Esto es normal al principio —
            no es un fallo.
          </p>
        </section>
      )}

      {/* Stats clave, en lenguaje llano */}
      <section className="flex flex-wrap gap-4 mb-6">
        <StatCard
          label="Aciertos históricos"
          value={balanced?.trade_metrics.win_rate !== null && balanced?.trade_metrics.win_rate !== undefined ? `${(balanced.trade_metrics.win_rate * 100).toFixed(0)}%` : "—"}
          hint={`de ${balanced?.trade_metrics.total_trades ?? 0} operaciones simuladas`}
        />
        <StatCard
          label="Resultado acumulado"
          value={
            balanced?.equity_metrics.total_return !== null && balanced?.equity_metrics.total_return !== undefined
              ? `${(balanced.equity_metrics.total_return * 100).toFixed(1)}%`
              : "—"
          }
          hint="sobre el capital simulado, coste incluido"
        />
        <StatCard label="Operaciones abiertas ahora" value={String(openPositions)} hint="simuladas esta semana (papel, sin dinero real)" />
        <StatCard label="Peor caída sufrida" value={balanced?.equity_metrics.max_drawdown !== null && balanced?.equity_metrics.max_drawdown !== undefined ? `${(balanced.equity_metrics.max_drawdown * 100).toFixed(1)}%` : "—"} hint="máxima pérdida temporal en la simulación" />
      </section>

      {/* Accesos rápidos */}
      <section className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Link href="/senales" className="border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 hover:border-neutral-400 dark:hover:border-neutral-600">
          <p className="font-medium mb-1">Ver señales →</p>
          <p className="text-xs text-neutral-500">Cada evento detectado, con su análisis completo y por qué se opera o no.</p>
        </Link>
        <Link href="/cartera" className="border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 hover:border-neutral-400 dark:hover:border-neutral-600">
          <p className="font-medium mb-1">Ver resultados →</p>
          <p className="text-xs text-neutral-500">Cómo le ha ido históricamente y qué está pasando esta semana.</p>
        </Link>
        <Link href="/como-funciona" className="border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 hover:border-neutral-400 dark:hover:border-neutral-600">
          <p className="font-medium mb-1">¿Cómo funciona esto? →</p>
          <p className="text-xs text-neutral-500">Explicación paso a paso del motor, sin necesitar conocer el proyecto de antes.</p>
        </Link>
      </section>
    </main>
  );
}
