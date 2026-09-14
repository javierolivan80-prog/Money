// SignalDetail.tsx — fila expandida de SignalsTable: el razonamiento COMPLETO
// que el pipeline ya calcula por evento (Bull/Bear/Judge, análogos
// históricos, EV, y sobre todo el "por qué NO_TRADE" de abstention_engine.py)
// pero que hasta ahora se descartaba en la UI salvo un par de frases sueltas.
//
// Diseño: nunca mostrar una probabilidad/EV sin su contexto de muestra al
// lado (n_historical_analogues) — mostrar "82%" sin decir sobre cuántos
// análogos se calculó es exactamente el tipo de falsa certeza que este
// proyecto evita en el resto del pipeline (ver historical_analogues.py).
import type { SignalFeedRow } from "@/lib/queries";

const STRATEGIES = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"] as const;

const DECISION_COLORS: Record<string, string> = {
  LONG: "text-green-700 dark:text-green-400",
  SHORT: "text-red-700 dark:text-red-400",
  NO_TRADE: "text-neutral-500",
};

function Section({ title, color, children }: { title: string; color: string; children: React.ReactNode }) {
  return (
    <div>
      <p className={`font-semibold mb-1 ${color}`}>{title}</p>
      <div className="space-y-1 text-neutral-700 dark:text-neutral-300">{children}</div>
    </div>
  );
}

function BulletList({ items }: { items: string[] | undefined }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="list-disc list-inside space-y-0.5">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

export function SignalDetail({ row, colSpan }: { row: SignalFeedRow; colSpan: number }) {
  const bull = row.bull_output;
  const bear = row.bear_output;
  const judge = row.judge_output;
  const novelty = row.novelty_reasoning;
  const impact = row.impact_estimation;
  const ev = row.ev_calculation;
  const abstention = row.abstention_decision;

  return (
    <tr className="border-b border-neutral-100 dark:border-neutral-900 bg-neutral-50 dark:bg-neutral-900/50">
      <td colSpan={colSpan} className="py-4 px-4 text-xs">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Bull */}
          <Section title="Bull" color="text-green-700 dark:text-green-400">
            {bull ? (
              <>
                <p>{bull.thesis}</p>
                {bull.upside_drivers?.length > 0 && (
                  <>
                    <p className="font-medium mt-1">Drivers:</p>
                    <BulletList items={bull.upside_drivers} />
                  </>
                )}
                {bull.catalysts_forward?.length > 0 && (
                  <>
                    <p className="font-medium mt-1">Catalizadores próximos:</p>
                    <BulletList items={bull.catalysts_forward} />
                  </>
                )}
                <p className="text-neutral-500 mt-1">Mercado direccionable: {bull.addressable_market}</p>
                <p className="text-neutral-500">Comparables: {bull.comparable_events}</p>
              </>
            ) : (
              <p className="text-neutral-400 italic">—</p>
            )}
          </Section>

          {/* Bear */}
          <Section title="Bear" color="text-red-700 dark:text-red-400">
            {bear ? (
              <>
                <p>{bear.counter_thesis}</p>
                {bear.downside_risks?.length > 0 && (
                  <>
                    <p className="font-medium mt-1">Riesgos:</p>
                    <BulletList items={bear.downside_risks} />
                  </>
                )}
                {bear.negative_catalysts?.length > 0 && (
                  <>
                    <p className="font-medium mt-1">Catalizadores negativos:</p>
                    <BulletList items={bear.negative_catalysts} />
                  </>
                )}
                <p className="text-neutral-500 mt-1">¿Ya descontado?: {bear.valuation_concern}</p>
                <p className="text-neutral-500">Precedente: {bear.historical_precedent}</p>
              </>
            ) : (
              <p className="text-neutral-400 italic">—</p>
            )}
          </Section>

          {/* Judge */}
          <Section title="Judge" color="text-neutral-900 dark:text-neutral-100">
            {judge ? (
              <>
                <p>
                  net_conviction={judge.net_conviction.toFixed(2)} · confidence={judge.confidence_in_conviction.toFixed(0)}%
                </p>
                <p className="text-neutral-500">Incertidumbre clave: {judge.key_uncertainty}</p>
                {judge.overriding_concern && <p className="text-neutral-500">Preocupación dominante: {judge.overriding_concern}</p>}
              </>
            ) : (
              <p className="text-neutral-400 italic">—</p>
            )}
          </Section>

          {/* Novelty */}
          <Section title="Novelty (¿qué tan sorpresa fue?)" color="text-purple-700 dark:text-purple-400">
            {novelty ? (
              <>
                <p>Score: {novelty.score}/100</p>
                {novelty.components_used?.length > 0 && <p className="text-neutral-500">Con datos de: {novelty.components_used.join(", ")}</p>}
                {novelty.components_unavailable?.length > 0 && (
                  <p className="text-amber-600 dark:text-amber-400">Sin datos de: {novelty.components_unavailable.join(", ")} (no penaliza ni favorece — se renormaliza)</p>
                )}
              </>
            ) : (
              <p className="text-neutral-400 italic">—</p>
            )}
          </Section>

          {/* Historical analogues / Impact estimation */}
          <Section title="Análogos históricos (Impact Estimation)" color="text-blue-700 dark:text-blue-400">
            {impact ? (
              <>
                <p className="font-medium">
                  n={row.n_historical_analogues ?? "?"} análogos · confianza={impact.confidence.toFixed(0)}/100
                </p>
                <p>{impact.expected_magnitude}</p>
                <p className="text-neutral-500">
                  P(±5%)={impact.probability_5pct_move.toFixed(0)}% · P(±10%)={impact.probability_10pct_move.toFixed(0)}% · P(±20%)=
                  {impact.probability_20pct_move.toFixed(0)}%
                </p>
                {(row.n_historical_analogues ?? 0) < 5 && (
                  <p className="text-amber-600 dark:text-amber-400">Muestra pequeña — estimación contraída hacia el prior de la clase (shrinkage).</p>
                )}
              </>
            ) : (
              <p className="text-neutral-400 italic">—</p>
            )}
          </Section>

          {/* EV reasoning */}
          <Section title="Expected Value" color="text-neutral-900 dark:text-neutral-100">
            {ev ? (
              <>
                <p className="text-neutral-500">{ev.reasoning}</p>
                <p className="mt-1">{ev.threshold_balanced}</p>
              </>
            ) : (
              <p className="text-neutral-400 italic">—</p>
            )}
          </Section>
        </div>

        {/* Abstention — el "por qué NO SIGNAL" por versión de estrategia */}
        <div className="mt-4 pt-3 border-t border-neutral-200 dark:border-neutral-800">
          <p className="font-semibold mb-1.5">Decisión por versión de estrategia</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {STRATEGIES.map((strategy) => {
              const d = abstention?.[strategy];
              return (
                <div key={strategy} className="border border-neutral-200 dark:border-neutral-800 rounded p-2">
                  <p className="font-medium">
                    {strategy}: <span className={DECISION_COLORS[d?.trade_decision ?? "NO_TRADE"]}>{d?.trade_decision ?? "—"}</span>
                  </p>
                  {d?.reason_if_no_trade ? (
                    <p className="text-neutral-500 mt-0.5">{d.reason_if_no_trade}</p>
                  ) : d?.trade_decision && d.trade_decision !== "NO_TRADE" ? (
                    <p className="text-neutral-500 mt-0.5">Pasó los 7 filtros de abstención — confidence={d.confidence.toFixed(0)}%</p>
                  ) : null}
                </div>
              );
            })}
          </div>
          {(row.entry_date || row.exit_date) && (
            <p className="text-neutral-500 mt-2">
              {row.entry_date && `Entrada ${row.entry_date}`}
              {row.exit_date && ` · Salida ${row.exit_date} (${row.exit_reason})`}
              {row.pnl_pct !== null && ` · P&L ${row.pnl_pct.toFixed(2)}%`}
            </p>
          )}
        </div>
      </td>
    </tr>
  );
}
