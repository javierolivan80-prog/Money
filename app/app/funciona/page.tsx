import { isDatabaseConfigured } from "@/lib/db";
import {
  getLatestPortfolioRunBatchTag,
  getPortfolioReport,
  getLatestValidationRunBatchTag,
  getValidationReport,
  type CalibrationDiagnostics,
  type EventStudyClassResult,
  type VersionDecision,
} from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { CalibrationCurve } from "@/components/CalibrationCurve";
import { ComparisonTable, type ComparisonRow } from "@/components/ComparisonTable";

export const dynamic = "force-dynamic";

// funciona/page.tsx — fusiona lo que antes eran tres pestañas separadas
// (Calibration, Validation, Comparison): las tres responden la misma
// pregunta de fondo, "¿me puedo fiar de esto?", solo que desde ángulos
// distintos (¿el evento mueve el precio de verdad?, ¿el modelo sabe cuándo
// está seguro?, ¿qué versión conviene más?) — tiene más sentido como tres
// secciones de una sola pantalla que como pestañas sueltas y sin conexión
// visible entre ellas.
const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const VERSION_LABELS: Record<string, string> = { CONSERVATIVE: "Conservador", AGGRESSIVE: "Agresivo", BALANCED: "Equilibrado" };

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
  baseline: "Situación normal",
  "commission_plus_0.1pct": "Con más comisiones (+0.1%)",
  "spread_plus_0.2pct": "Con más diferencia compra/venta (+0.2%)",
  latency_d_plus_2: "Entrando un día más tarde",
  "confidence_minus_20pct": "Si el modelo fuera menos seguro (-20%)",
  high_vix_regime: "En mercado muy volátil",
  low_vix_regime: "En mercado tranquilo",
};
const SCENARIO_ORDER = Object.keys(SCENARIO_LABELS);

const OVER_UNDER_CONFIDENCE_THRESHOLD_PP = 5.0;

function interpretCalibration(diag: CalibrationDiagnostics): { label: string; adjustment: string } {
  if (diag.buckets.length === 0 || diag.n < 5) {
    return { label: "Muestra insuficiente para interpretar", adjustment: "Esperar más operaciones antes de sacar conclusiones." };
  }
  const totalN = diag.buckets.reduce((s, b) => s + b.n, 0);
  const meanConfidence = diag.buckets.reduce((s, b) => s + b.mean_confidence * b.n, 0) / totalN;
  const meanHitRatePct = (diag.buckets.reduce((s, b) => s + b.hit_rate * b.n, 0) / totalN) * 100;
  const diff = meanConfidence - meanHitRatePct;

  if (diff > OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: `Exceso de confianza — dice estar ${meanConfidence.toFixed(0)}% seguro pero acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
      adjustment: `Conviene desconfiar un poco de las confianzas altas que reporta (~${diff.toFixed(0)} puntos de más).`,
    };
  }
  if (diff < -OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: `Confianza baja de más — dice ${meanConfidence.toFixed(0)}% pero acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
      adjustment: "Acierta más de lo que dice — no hace falta corregir a la baja.",
    };
  }
  return {
    label: `Bien calibrado — dice ${meanConfidence.toFixed(0)}% y acierta el ${meanHitRatePct.toFixed(0)}% de las veces`,
    adjustment: "No hace falta ningún ajuste — cuando dice que está seguro, suele tener razón.",
  };
}

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
      <td className="py-2 pr-4 text-right">{stats.p_value !== null ? stats.p_value.toFixed(4) : "—"}</td>
      <td className="py-2 pr-4 text-center">
        {stats.significant === true && <span className="text-green-600 dark:text-green-400">✓ sí</span>}
        {stats.significant === false && <span className="text-red-500">✗ no</span>}
        {stats.significant === null && <span className="text-neutral-400">? aún no se sabe</span>}
      </td>
      <td className="py-2 text-neutral-500">{stats.conclusion}</td>
    </tr>
  );
}

export default async function FuncionaPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/funciona" />
        <h1 className="text-2xl font-bold mb-4">¿Funciona?</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const [portfolioTag, validationTag] = await Promise.all([getLatestPortfolioRunBatchTag(), getLatestValidationRunBatchTag()]);

  if (!portfolioTag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/funciona" />
        <h1 className="text-2xl font-bold mb-4">¿Funciona?</h1>
        <p className="text-sm text-neutral-500">Todavía no hay ningún backtest de cartera registrado.</p>
      </main>
    );
  }

  const [report, validationReport] = await Promise.all([
    getPortfolioReport(portfolioTag),
    validationTag ? getValidationReport(validationTag) : Promise.resolve(null),
  ]);

  if (!report) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/funciona" />
        <h1 className="text-2xl font-bold mb-4">¿Funciona?</h1>
        <p className="text-sm text-neutral-500">
          No se pudo leer el reporte para <code>{portfolioTag}</code>.
        </p>
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

  const comparisonRows: ComparisonRow[] = [
    { metric: "Resultado total", conservative: fmtPct(v.CONSERVATIVE.equity_metrics.total_return), aggressive: fmtPct(v.AGGRESSIVE.equity_metrics.total_return), balanced: fmtPct(v.BALANCED.equity_metrics.total_return) },
    { metric: "Sharpe (retorno vs riesgo)", conservative: fmtRatio(v.CONSERVATIVE.equity_metrics.sharpe_ratio), aggressive: fmtRatio(v.AGGRESSIVE.equity_metrics.sharpe_ratio), balanced: fmtRatio(v.BALANCED.equity_metrics.sharpe_ratio) },
    { metric: "Acierto", conservative: fmtPct(v.CONSERVATIVE.trade_metrics.win_rate), aggressive: fmtPct(v.AGGRESSIVE.trade_metrics.win_rate), balanced: fmtPct(v.BALANCED.trade_metrics.win_rate) },
    { metric: "Peor caída", conservative: fmtPct(v.CONSERVATIVE.equity_metrics.max_drawdown), aggressive: fmtPct(v.AGGRESSIVE.equity_metrics.max_drawdown), balanced: fmtPct(v.BALANCED.equity_metrics.max_drawdown) },
    { metric: "Operaciones/año", conservative: tradesPerYear("CONSERVATIVE"), aggressive: tradesPerYear("AGGRESSIVE"), balanced: tradesPerYear("BALANCED") },
    { metric: "Recomendada para", conservative: "Evitar riesgo", aggressive: "Buscar más ganancia", balanced: "Término medio" },
  ];

  const eventClasses = validationReport ? Object.keys(validationReport.event_study).sort() : [];

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/funciona" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">¿Funciona?</h1>
        <p className="text-sm text-neutral-500 mt-1">Todo lo que responde si te puedes fiar del sistema, y por qué.</p>
      </header>

      {/* Veredicto + decisión por versión */}
      {validationReport && (
        <section className="mb-10">
          <div className={`border-2 rounded-lg p-5 mb-4 ${OPTION_COLORS[validationReport.best_decision.option]}`}>
            <p className="text-xs uppercase tracking-wide text-neutral-500 mb-1">
              Veredicto global · versión recomendada: {VERSION_LABELS[validationReport.best_version]}
            </p>
            <p className={`text-2xl font-bold mb-2 ${OPTION_TEXT_COLORS[validationReport.best_decision.option]}`}>{validationReport.best_decision.label}</p>
            <p className="text-sm">{validationReport.best_decision.recommendation}</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {VERSION_ORDER.filter((ver) => validationReport.decisions[ver]).map((ver) => (
              <DecisionCard key={ver} title={VERSION_LABELS[ver]} decision={validationReport.decisions[ver]} />
            ))}
          </div>
        </section>
      )}

      {/* Event study */}
      {validationReport && (
        <section className="mb-10">
          <h2 className="text-lg font-semibold mb-1">¿El tipo de evento mueve el precio de verdad?</h2>
          <p className="text-xs text-neutral-500 mb-3">
            Sobre TODOS los eventos detectados, no solo los que se operaron. "Sí" significa que el movimiento no parece casualidad; "aún no
            se sabe" significa que hacen falta más casos para estar seguros — no que no haya efecto.
          </p>
          {eventClasses.length === 0 ? (
            <p className="text-sm text-neutral-400 italic">Sin datos todavía.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-sm">
                <thead>
                  <tr className="border-b border-neutral-300 dark:border-neutral-700 text-neutral-500">
                    <th className="py-2 pr-4 font-medium">Tipo de evento</th>
                    <th className="py-2 pr-4 font-medium text-right">Casos</th>
                    <th className="py-2 pr-4 font-medium text-right">Movimiento típico</th>
                    <th className="py-2 pr-4 font-medium text-right">p-value</th>
                    <th className="py-2 pr-4 font-medium text-center">¿Es real?</th>
                    <th className="py-2 font-medium">Explicación</th>
                  </tr>
                </thead>
                <tbody>
                  {eventClasses.map((ec) => (
                    <EventStudyRow key={ec} eventClass={ec} stats={validationReport.event_study[ec]} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* Calibración */}
      <section className="mb-10">
        <h2 className="text-lg font-semibold mb-1">¿El sistema sabe cuándo está seguro?</h2>
        <p className="text-xs text-neutral-500 mb-3">Cuando dice "80% de confianza", ¿acierta de verdad el 80% de las veces?</p>
        <div className="flex flex-col md:flex-row gap-4">
          {VERSION_ORDER.filter((ver) => report.versions[ver]).map((ver) => {
            const diag = report.versions[ver].confidence_calibration;
            const { label, adjustment } = interpretCalibration(diag);
            return (
              <div key={ver} className="flex-1 min-w-[300px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
                <h3 className="font-semibold mb-3">{VERSION_LABELS[ver]}</h3>
                <CalibrationCurve buckets={diag.buckets} />
                <p className="text-[10px] text-neutral-400 mt-1 mb-3">Línea gris = calibración perfecta · tamaño del punto = nº de casos</p>
                <p className="text-sm font-medium mb-1">{label}</p>
                <p className="text-xs text-neutral-500">{adjustment}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* Sensibilidad */}
      {validationReport && (
        <section className="mb-10">
          <h2 className="text-lg font-semibold mb-1">¿Se rompe el resultado si las condiciones empeoran?</h2>
          <p className="text-xs text-neutral-500 mb-3">Un resultado que se mantiene positivo en todos los escenarios es más de fiar que uno que solo funciona en el mejor de los casos.</p>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="border-b border-neutral-300 dark:border-neutral-700 text-neutral-500">
                  <th className="py-2 pr-4 font-medium">Escenario</th>
                  <th className="py-2 pr-4 font-medium text-right">Conservador</th>
                  <th className="py-2 pr-4 font-medium text-right">Agresivo</th>
                </tr>
              </thead>
              <tbody>
                {SCENARIO_ORDER.map((key) => {
                  const cons = validationReport.sensitivity.scenarios.CONSERVATIVE?.[key];
                  const aggr = validationReport.sensitivity.scenarios.AGGRESSIVE?.[key];
                  return (
                    <tr key={key} className="border-b border-neutral-100 dark:border-neutral-900">
                      <td className="py-2 pr-4">{SCENARIO_LABELS[key]}</td>
                      <td className="py-2 pr-4 text-right">
                        {cons?.total_return !== null && cons?.total_return !== undefined ? (
                          <span className={cons.total_return >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>{(cons.total_return * 100).toFixed(2)}%</span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right">
                        {aggr?.total_return !== null && aggr?.total_return !== undefined ? (
                          <span className={aggr.total_return >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>{(aggr.total_return * 100).toFixed(2)}%</span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Comparación de versiones */}
      <section>
        <h2 className="text-lg font-semibold mb-3">¿Qué versión conviene?</h2>
        <ComparisonTable rows={comparisonRows} />
      </section>
    </main>
  );
}
