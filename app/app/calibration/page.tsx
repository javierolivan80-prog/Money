import { isDatabaseConfigured } from "@/lib/db";
import { getLatestPortfolioRunBatchTag, getPortfolioReport, type CalibrationDiagnostics } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { CalibrationCurve } from "@/components/CalibrationCurve";

export const dynamic = "force-dynamic";

const VERSION_ORDER = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;
const VERSION_LABELS: Record<string, string> = { CONSERVATIVE: "Conservative", AGGRESSIVE: "Aggressive", BALANCED: "Balanced" };

// Umbral de "sesgo sistemático" para la interpretación textual — heurístico,
// documentado como tal (mismo principio que el resto del proyecto: una
// convención razonable, no una prueba estadística formal). 5 puntos
// porcentuales de diferencia entre confidence media y win rate real.
const OVER_UNDER_CONFIDENCE_THRESHOLD_PP = 5.0;

function interpret(diag: CalibrationDiagnostics): { label: string; adjustment: string } {
  if (diag.buckets.length === 0 || diag.n < 5) {
    return { label: "Muestra insuficiente para interpretar", adjustment: "Esperar más trades antes de ajustar nada." };
  }
  const totalN = diag.buckets.reduce((s, b) => s + b.n, 0);
  const meanConfidence = diag.buckets.reduce((s, b) => s + b.mean_confidence * b.n, 0) / totalN;
  const meanHitRatePct = diag.buckets.reduce((s, b) => s + b.hit_rate * b.n, 0) / totalN * 100;
  const diff = meanConfidence - meanHitRatePct;

  if (diff > OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: `Overconfident — confidence media ${meanConfidence.toFixed(0)}% vs win rate real ${meanHitRatePct.toFixed(0)}%`,
      adjustment: `Considera restar ~${diff.toFixed(0)} puntos a la confidence reportada por el modelo antes de usarla para sizing.`,
    };
  }
  if (diff < -OVER_UNDER_CONFIDENCE_THRESHOLD_PP) {
    return {
      label: `Underconfident — confidence media ${meanConfidence.toFixed(0)}% vs win rate real ${meanHitRatePct.toFixed(0)}%`,
      adjustment: `El modelo acierta más de lo que dice — no hace falta corregir a la baja; si acaso, dar más peso a confidence alta.`,
    };
  }
  return {
    label: `Bien calibrado — confidence media ${meanConfidence.toFixed(0)}% ≈ win rate real ${meanHitRatePct.toFixed(0)}%`,
    adjustment: "No se detecta sesgo sistemático — no se recomienda ningún ajuste.",
  };
}

export default async function CalibrationPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/calibration" />
        <h1 className="text-2xl font-bold mb-4">Calibration</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const tag = await getLatestPortfolioRunBatchTag();
  if (!tag) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/calibration" />
        <h1 className="text-2xl font-bold mb-4">Calibration</h1>
        <p className="text-sm text-neutral-500">Todavía no hay ningún backtest de cartera registrado.</p>
      </main>
    );
  }

  const report = await getPortfolioReport(tag);
  if (!report) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/calibration" />
        <h1 className="text-2xl font-bold mb-4">Calibration</h1>
        <p className="text-sm text-neutral-500">No se pudo leer el reporte para <code>{tag}</code>.</p>
      </main>
    );
  }

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/calibration" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Calibration</h1>
        <p className="text-sm text-neutral-500 mt-1">
          ¿Cuando el modelo dice X% de confianza, acierta X% de las veces? Corrida:{" "}
          <code className="font-mono">{tag}</code>
        </p>
      </header>

      <div className="flex flex-col md:flex-row gap-4">
        {VERSION_ORDER.filter((v) => report.versions[v]).map((version) => {
          const diag = report.versions[version].confidence_calibration;
          const { label, adjustment } = interpret(diag);
          return (
            <div key={version} className="flex-1 min-w-[300px] border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
              <h2 className="text-lg font-semibold mb-3">{VERSION_LABELS[version]}</h2>

              <CalibrationCurve buckets={diag.buckets} />
              <p className="text-[10px] text-neutral-400 mt-1 mb-3">
                Línea gris = calibración perfecta · tamaño del punto = n del bucket
              </p>

              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-sm mb-3">
                <dt className="text-neutral-500" title="Correlación de Pearson entre confidence y acierto (1 = perfecto)">
                  Calibration score
                </dt>
                <dd className="text-right font-mono">{diag.correlation !== null ? diag.correlation.toFixed(2) : "—"}</dd>

                <dt className="text-neutral-500" title="mean((confidence/100 - acierto)^2) — 0 = perfecto, 0.25 = referencia de 'siempre 50%'">
                  Brier score
                </dt>
                <dd className="text-right font-mono">{diag.brier_score !== null ? diag.brier_score.toFixed(3) : "—"}</dd>

                <dt className="text-neutral-500" title="Expected Calibration Error — promedio ponderado de |hit_rate - confidence| por bucket">
                  ECE
                </dt>
                <dd className="text-right font-mono">{diag.ece !== null ? diag.ece.toFixed(3) : "—"}</dd>

                <dt className="text-neutral-500" title="Fórmula distinta (1-|predicho-real|/|predicho| sobre EV vs retorno) — ver RUNBOOK.md §3.10">
                  Calibración (Fase 3)
                </dt>
                <dd className="text-right font-mono">{report.versions[version].calibration.calibration_score?.toFixed(2) ?? "—"}</dd>
              </dl>

              <p className="text-sm font-medium mb-1">{label}</p>
              <p className="text-xs text-neutral-500">{adjustment}</p>
            </div>
          );
        })}
      </div>
    </main>
  );
}
