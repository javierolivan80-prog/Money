"use client";

// QualityCard.tsx — una empresa en el ranking de largo plazo: nota global,
// barra por criterio y la explicación en texto de cada uno (redactada en
// quality_score.py, no aquí).
//
// Los criterios no calculables se muestran en gris con su motivo, en vez de
// ocultarse: que falte un dato es información, y esconderlo haría parecer que
// la nota se apoya en más criterios de los que realmente usa.
import { useState } from "react";
import type { QualityScoreRow } from "@/lib/queries";

function barColor(score: number | null): string {
  if (score === null) return "bg-neutral-300 dark:bg-neutral-700";
  if (score >= 75) return "bg-green-500";
  if (score >= 55) return "bg-lime-500";
  if (score >= 35) return "bg-amber-500";
  return "bg-red-500";
}

export function QualityCard({ row, rank, scoreColorClass }: { row: QualityScoreRow; rank: number; scoreColorClass: string }) {
  const [open, setOpen] = useState(false);
  const nDisponibles = row.components.filter((c) => c.score !== null).length;

  return (
    <div className="border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden">
      <button onClick={() => setOpen(!open)} className="w-full text-left p-4 hover:bg-neutral-50 dark:hover:bg-neutral-900/50">
        <div className="flex items-center gap-4">
          <span className="text-xs text-neutral-400 w-6">{rank}</span>
          <div className="flex-1 min-w-0">
            <p className="font-mono font-medium">{row.ticker}</p>
            {row.company_name && <p className="text-xs text-neutral-500 truncate">{row.company_name}</p>}
          </div>
          <div className="text-right">
            <p className={`text-2xl font-bold ${scoreColorClass}`}>{row.total_score !== null ? row.total_score.toFixed(0) : "—"}</p>
            <p className="text-[10px] text-neutral-400">
              {nDisponibles}/5 criterios · {row.n_years} {row.n_years === 1 ? "ejercicio" : "ejercicios"}
            </p>
          </div>
          <span className="text-neutral-400">{open ? "▾" : "▸"}</span>
        </div>
        <p className="text-xs text-neutral-600 dark:text-neutral-400 mt-2 ml-10">{row.verdict}</p>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-neutral-100 dark:border-neutral-900 space-y-3">
          {row.components.map((c) => (
            <div key={c.name}>
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span className={`text-sm font-medium ${c.score === null ? "text-neutral-400" : ""}`}>{c.name}</span>
                <span className="text-xs font-mono text-neutral-500">{c.score !== null ? `${c.score.toFixed(0)}/100` : "sin datos"}</span>
              </div>
              <div className="bg-neutral-200 dark:bg-neutral-800 rounded h-1.5 mb-1">
                <div className={`h-1.5 rounded ${barColor(c.score)}`} style={{ width: `${c.score ?? 0}%` }} />
              </div>
              <p className="text-xs text-neutral-600 dark:text-neutral-400">{c.explanation}</p>
            </div>
          ))}
          {row.price_used !== null && (
            <p className="text-[10px] text-neutral-400 pt-1">Valorada con un precio de {row.price_used.toFixed(2)} por acción.</p>
          )}
        </div>
      )}
    </div>
  );
}
