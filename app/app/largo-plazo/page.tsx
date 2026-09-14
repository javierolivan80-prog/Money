import { isDatabaseConfigured } from "@/lib/db";
import { getLatestQualityScoreDate, getQualityScores, type QualityScoreRow } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { QualityCard } from "@/components/QualityCard";

export const dynamic = "force-dynamic";

// largo-plazo/page.tsx — la pestaña de análisis fundamental. A diferencia del
// resto del dashboard (que gira alrededor de eventos y del corto plazo), esto
// ordena empresas por calidad del negocio y precio, con horizonte de años.
//
// Todas las explicaciones que se muestran vienen redactadas desde Python
// (quality_score.py) — aquí no se reformula ni se recalcula nada, por el
// mismo motivo que el resto del dashboard: una sola fuente de verdad por
// número y por frase.

function scoreColor(score: number | null): string {
  if (score === null) return "text-neutral-400";
  if (score >= 75) return "text-green-700 dark:text-green-400";
  if (score >= 55) return "text-lime-700 dark:text-lime-400";
  if (score >= 35) return "text-amber-700 dark:text-amber-400";
  return "text-red-700 dark:text-red-400";
}

export default async function LargoPlazoPage() {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/largo-plazo" />
        <h1 className="text-2xl font-bold mb-4">Largo plazo</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const asOfDate = await getLatestQualityScoreDate();

  if (!asOfDate) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/largo-plazo" />
        <h1 className="text-2xl font-bold mb-4">Largo plazo</h1>
        <div className="border border-neutral-300 dark:border-neutral-700 rounded-lg p-4">
          <p className="font-medium mb-2">Todavía no se han analizado las cuentas de ninguna empresa.</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            El pipeline nocturno descarga las cuentas anuales de la SEC y calcula las notas. Vuelve después de la próxima ejecución.
          </p>
        </div>
      </main>
    );
  }

  const scores: QualityScoreRow[] = await getQualityScores(asOfDate);
  const conNota = scores.filter((s) => s.total_score !== null);
  const sinNota = scores.filter((s) => s.total_score === null);

  return (
    <main className="max-w-5xl mx-auto p-6">
      <Nav active="/largo-plazo" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Largo plazo</h1>
        <p className="text-sm text-neutral-500 mt-1">
          Empresas ordenadas por calidad de negocio y precio, según sus cuentas anuales auditadas. Horizonte de años, no de días.
          Datos a {asOfDate} · {scores.length} empresas analizadas.
        </p>
      </header>

      {/* Aviso: esto no es asesoramiento */}
      <div className="border border-amber-300 bg-amber-50 dark:bg-amber-950 dark:border-amber-800 rounded-lg p-4 mb-6 text-sm">
        <p className="font-medium mb-1">Esto no es una recomendación de inversión</p>
        <p className="text-neutral-700 dark:text-neutral-300">
          Es un resumen estructurado de cuentas públicas, calculado automáticamente. Una nota alta significa que la empresa cumple
          criterios clásicos de calidad y valoración — no que su acción vaya a subir. Los criterios son convenciones del análisis
          fundamental, no reglas optimizadas sobre este histórico.
        </p>
      </div>

      {/* Cómo se lee la nota */}
      <section className="mb-6 border border-neutral-200 dark:border-neutral-800 rounded-lg p-4">
        <p className="font-medium text-sm mb-2">Los 5 criterios, en cristiano</p>
        <ul className="text-xs text-neutral-600 dark:text-neutral-400 space-y-1">
          <li>
            <strong>Rentabilidad</strong> — ¿cuánto gana por cada euro de capital propio? (más es mejor)
          </li>
          <li>
            <strong>Solidez financiera</strong> — ¿cuánta deuda arrastra? (menos es mejor)
          </li>
          <li>
            <strong>Calidad del beneficio</strong> — ¿el beneficio contable se convierte en caja real? (si no, mala señal)
          </li>
          <li>
            <strong>Crecimiento</strong> — ¿vende más cada año?
          </li>
          <li>
            <strong>Precio</strong> — ¿cuánto se paga por cada euro de beneficio? (el PER; menos es mejor)
          </li>
        </ul>
        <p className="text-xs text-neutral-500 mt-2">
          Si un criterio no se puede calcular porque la empresa no publica ese dato, <strong>no puntúa cero</strong>: se excluye y los
          demás se reparten el peso. Penalizar la falta de información castigaría justo a las empresas peor documentadas.
        </p>
      </section>

      {/* Ranking */}
      {conNota.length === 0 ? (
        <p className="text-sm text-neutral-500 italic mb-6">Ninguna empresa tiene datos suficientes para una nota todavía.</p>
      ) : (
        <section className="space-y-3 mb-8">
          {conNota.map((row, i) => (
            <QualityCard key={row.cik} row={row} rank={i + 1} scoreColorClass={scoreColor(row.total_score)} />
          ))}
        </section>
      )}

      {/* Sin datos suficientes — visibles, no escondidas */}
      {sinNota.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold mb-2 text-neutral-500">Sin datos suficientes ({sinNota.length})</h2>
          <p className="text-xs text-neutral-500 mb-2">
            Estas empresas no publican (todavía) suficientes magnitudes en sus cuentas para calcular ni un criterio. No significa que
            sean malas — significa que no se sabe.
          </p>
          <p className="text-xs font-mono text-neutral-400">{sinNota.map((s) => s.ticker).join(" · ")}</p>
        </section>
      )}
    </main>
  );
}
