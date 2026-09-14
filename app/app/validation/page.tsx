import { isDatabaseConfigured } from "@/lib/db";
import { getLatestValidationRunBatchTag, getValidationReport, type EventStudyClassResult, type VersionDecision } from "@/lib/queries";
import { Nav } from "@/components/Nav";

export const dynamic = "force-dynamic";

const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const VERSION_LABELS: Record<string, string> = { CONSERVATIVE: "Conservative", AGGRESSIVE: "Aggressive", BALANCED: "Balanced" };

const OPTION_COLORS: Record<string, string> = {
  A: "border-green-300 dark:border-green-800 bg-green-50 dark:bg-green-950",
  B: "border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950",
  C: "border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950",
};

const OPTION_TEXT_COLORS: Record<string, string> = {
  A: "text-green-700 dark:text-green-400",
  B: "text-amber-700 dark:text-amber-400",
  C: "text-red-700 dark:text-red-400",
};

const SCENARIO_LABELS: Record<string, string> = {
  baseline: "Baseline",
  "commission_plus_0.1pct": "Comisiones +0.1%",
  "spread_plus_0.2pct": "Spread +0.2%",
  latency_d_plus_2: "Entrada D+2 (latencia)",
  "confidence_minus_20pct": "Confidence -20%",
  high_vix_regime: "Régimen alto-VIX",
  low_vix_regime: "Régimen bajo-VIX",
};
const SCENARIO_ORDER = Object.keys(SCENARIO_LABELS);

function DecisionCard({ title, decision }: { title: string; decision: VersionDecision }) {
  return (
    <div className={`border rounded-lg p-4 ${OPTION_COLORS[decision.option]}`}>
      <p className="text-sm text-neutral-500 mb-1">{title}</p>
      <p className={`text-lg font-bold mb-2 ${OPTION_TEXT_COLORS[decision.option]}`}>{decision.label}</p>
      <p className="text-sm mb-2">{decision.recommendation}</p>
      <ul className="text-xs text-neutral-600 dark:text-neutral-400 list-disc list-inside space-y-0.5">
        {decision.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </div>
  );
}

function EventStudyRow({ eventClass, stats }: { eventClass: string; stats: EventStudyClassResult }) {
  return (
    <tr className="border-b border-neutral-100 dark:border-neutral-900">
      <td className="py-2 pr-4 font-mono text-xs">{eventClass.replace(/^8K_/, "")}</td>
      <td className="py-2 pr-4 text-right">{stats.n}</td>
      <td className="py-2 pr-4 text-right">{stats.median_return_pct !== null ? `${stats.median_return_pct.toFixed(2)}%` : "—"}</td>
      <td className="py-2 pr-4 text-right">{stats.sigma_pct !== null ? `${stats.sigma_pct.toFixed(1)}%` : "—"}</td>
      <td className="py-2 pr-4 text-right">{stats.mde_pct !== null ? `${stats.mde_pct.toFixed(0)} bps` : "—"}</td>
      <td className="py-2 pr-4 text-right font-mono">{stats.p_value !== null ? stats.p_value.toFixed(4) : "—"}</td>
      <td className="py-2 pr-4 text-center">
        {stats.significant === true && <span className="text-green-600 dark:text-green-400">✓</span>}
        {stats.significant === false && <span className="text-red-500">✗</span>}
        {stats.significant === null && <span className="text-neutral-400">?</span>}
      </td>
      <td className="py-2 text-neutral-500">{stats.conclusion}</td>
    </tr>
  );
}

export default async function ValidationPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/validation" />
        <h1 className="text-2xl font-bold mb-4">Validation</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const tag = await getLatestValidationRunBatchTag();
  if (!tag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/validation" />
        <h1 className="text-2xl font-bold mb-4">Validation</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no hay ningún reporte de validación registrado.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Corre <code>python -m pipeline.validation.report --persist-only</code> (o espera al pipeline nocturno, que ya lo incluye tras el
            backtest de cartera) para generar el primero.
          </p>
        </div>
      </main>
    );
  }

  const report = await getValidationReport(tag);
  if (!report) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/validation" />
        <h1 className="text-2xl font-bold mb-4">Validation</h1>
        <p className="text-sm text-neutral-500">
          No se pudo leer el reporte para <code>{tag}</code>.
        </p>
      </main>
    );
  }

  const eventClasses = Object.keys(report.event_study).sort();

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/validation" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Validation</h1>
        <p className="text-sm text-neutral-500 mt-1">
          ¿Existe de verdad un edge, y sobrevive a costes y a supuestos razonables? Corrida:{" "}
          <code className="font-mono">{tag}</code> · generado {new Date(report.generated_at).toLocaleString("es-ES")}
        </p>
      </header>

      {/* Veredicto global */}
      <section className="mb-8">
        <div className={`border-2 rounded-lg p-5 ${OPTION_COLORS[report.best_decision.option]}`}>
          <p className="text-xs uppercase tracking-wide text-neutral-500 mb-1">Veredicto global · versión recomendada: {VERSION_LABELS[report.best_version]}</p>
          <p className={`text-2xl font-bold mb-2 ${OPTION_TEXT_COLORS[report.best_decision.option]}`}>{report.best_decision.label}</p>
          <p className="text-sm">{report.best_decision.recommendation}</p>
        </div>
        <p className="text-xs text-neutral-400 mt-2 italic">
          Los números de esta página vienen de los datos reales acumulados en la BD hasta hoy. Con pocos días de histórico, el veredicto más
          probable es YELLOWLIGHT o REDLIGHT por falta de muestra (n bajo) — no porque no haya edge, sino porque aún no hay evidencia
          suficiente para afirmarlo ni descartarlo (ver columna MDE abajo).
        </p>
      </section>

      {/* Decisión por versión */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Decisión por versión de estrategia</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {VERSION_ORDER.filter((v) => report.decisions[v]).map((v) => (
            <DecisionCard key={v} title={VERSION_LABELS[v]} decision={report.decisions[v]} />
          ))}
        </div>
      </section>

      {/* Event Study */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-1">Event Study — ¿el evento en sí mueve el precio?</h2>
        <p className="text-xs text-neutral-500 mb-3">
          Sobre TODOS los eventos con CAR calculado (ventana 20 días), no solo los que se operaron — la pregunta de fondo, con la mayor n
          posible. MDE = 2.8·σ/√n (80% potencia, α=0.05): el efecto mínimo que esta muestra podría detectar si existiera. Una clase con
          p-value ≥ 0.05 no está descartada — puede que el efecto real sea más pequeño que el MDE, no cero.
        </p>
        {eventClasses.length === 0 ? (
          <p className="text-sm text-neutral-400 italic">Sin datos de car_results todavía.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="border-b border-neutral-300 dark:border-neutral-700 text-neutral-500">
                  <th className="py-2 pr-4 font-medium">Event class</th>
                  <th className="py-2 pr-4 font-medium text-right">n</th>
                  <th className="py-2 pr-4 font-medium text-right">Mediana</th>
                  <th className="py-2 pr-4 font-medium text-right">σ</th>
                  <th className="py-2 pr-4 font-medium text-right">MDE</th>
                  <th className="py-2 pr-4 font-medium text-right">p-value</th>
                  <th className="py-2 pr-4 font-medium text-center">Sig.</th>
                  <th className="py-2 font-medium">Conclusión</th>
                </tr>
              </thead>
              <tbody>
                {eventClasses.map((ec) => (
                  <EventStudyRow key={ec} eventClass={ec} stats={report.event_study[ec]} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Sensitivity */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-1">Sensibilidad — ¿es frágil el resultado?</h2>
        <p className="text-xs text-neutral-500 mb-3">
          Cada escenario reprocesa los trades YA simulados de Conservative/Aggressive bajo un supuesto más adverso (ver
          pipeline/backtest/sensitivity.py). Un retorno que se mantiene positivo en todos los escenarios es más robusto que uno que solo
          sobrevive en el baseline.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-sm">
            <thead>
              <tr className="border-b border-neutral-300 dark:border-neutral-700 text-neutral-500">
                <th className="py-2 pr-4 font-medium">Escenario</th>
                <th className="py-2 pr-4 font-medium text-right">Conservative</th>
                <th className="py-2 pr-4 font-medium text-right">Aggressive</th>
              </tr>
            </thead>
            <tbody>
              {SCENARIO_ORDER.map((key) => {
                const cons = report.sensitivity.scenarios.CONSERVATIVE?.[key];
                const aggr = report.sensitivity.scenarios.AGGRESSIVE?.[key];
                return (
                  <tr key={key} className="border-b border-neutral-100 dark:border-neutral-900">
                    <td className="py-2 pr-4">{SCENARIO_LABELS[key]}</td>
                    <td className="py-2 pr-4 text-right">
                      {cons?.total_return !== null && cons?.total_return !== undefined ? (
                        <span className={cons.total_return >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>
                          {(cons.total_return * 100).toFixed(2)}%
                        </span>
                      ) : (
                        "—"
                      )}
                      <span className="text-neutral-400 text-xs ml-1">(n={cons?.n_trades ?? 0})</span>
                    </td>
                    <td className="py-2 pr-4 text-right">
                      {aggr?.total_return !== null && aggr?.total_return !== undefined ? (
                        <span className={aggr.total_return >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>
                          {(aggr.total_return * 100).toFixed(2)}%
                        </span>
                      ) : (
                        "—"
                      )}
                      <span className="text-neutral-400 text-xs ml-1">(n={aggr?.n_trades ?? 0})</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Bias */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Sesgos de datos</h2>
        <p className="text-sm text-neutral-500">
          {report.bias_report.n_delisted}/{report.bias_report.n_total_tickers} tickers deslistados (
          {report.bias_report.survivorship_bias_pct?.toFixed(1) ?? "—"}% posible sesgo de supervivencia) · {report.bias_report.n_price_gaps}/
          {report.bias_report.n_price_rows} filas de precio con gap ({report.bias_report.data_gap_pct?.toFixed(1) ?? "—"}%). Detalle completo en{" "}
          <a href="/portfolio" className="underline">
            Backtest Analysis
          </a>
          .
        </p>
      </section>
    </main>
  );
}
