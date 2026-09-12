"use client";

// CombinedEquityChart.tsx — curva de equity del backtest histórico +
// overlay de paper trading de la semana (spec Fase 5: "Equity curve
// (backtest historical + paper trading overlay)"). Primer uso de Recharts
// en el proyecto — pedido explícitamente por el spec de la Fase 5 (a
// diferencia del spec de la Fase 1, que pedía deliberadamente evitar
// dependencias de charting para un POC de una semana; aquí el propio
// usuario nombra Recharts, así que se sigue esa instrucción más reciente).
//
// Ambas series se expresan en % de retorno acumulado (no en $): el
// histórico parte de starting_capital, el paper trading no tiene sizing en
// dólares (ver paper_trading/simulator.py) — % es la única unidad común. El
// tramo de paper trading continúa desde el último % del histórico, no
// desde 0, para que la línea sea visualmente una sola continuación.
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PaperClosedTrade, PortfolioEquityPoint } from "@/lib/queries";

interface Point {
  seq: number;
  date: string;
  historical: number | null;
  paper: number | null;
}

export function CombinedEquityChart({
  equityCurve,
  startingCapital,
  paperClosedTrades,
}: {
  equityCurve: PortfolioEquityPoint[];
  startingCapital: number;
  paperClosedTrades: PaperClosedTrade[];
}) {
  // Eje X por POSICIÓN (seq), no por fecha real: el panel de precios del
  // backtest histórico puede seguir cargado más allá de la semana de paper
  // trading (p.ej. un backfill que ya trajo días futuros) — usar la fecha
  // como categoría duplicaría/desordenaría el eje de Recharts y rompía la
  // línea (bug real encontrado al probar con datos sintéticos que sí
  // solapaban rango de fechas). Un índice secuencial concatena las dos
  // series sin ambigüedad, sea cual sea el solape real de fechas.
  const historicalPoints: Point[] = equityCurve.map((p, i) => ({
    seq: i,
    date: p.trade_date,
    historical: ((p.balance - startingCapital) / startingCapital) * 100,
    paper: null,
  }));

  const lastHistoricalPct = historicalPoints.length > 0 ? historicalPoints[historicalPoints.length - 1].historical! : 0;
  const sortedPaper = [...paperClosedTrades].sort((a, b) => a.exit_date.localeCompare(b.exit_date));
  let cumulative = lastHistoricalPct;
  const baseSeq = historicalPoints.length;
  const paperPoints: Point[] = sortedPaper.map((t, i) => {
    cumulative += t.pnl_pct;
    return { seq: baseSeq + i, date: t.exit_date, historical: null, paper: cumulative };
  });

  // Punto de unión: el último histórico también lleva el valor de "paper"
  // para que Recharts dibuje una línea continua entre ambos tramos en vez
  // de un salto en blanco.
  const bridge: Point[] = historicalPoints.length > 0 ? [{ ...historicalPoints[historicalPoints.length - 1], paper: lastHistoricalPct }] : [];

  const data = [...historicalPoints.slice(0, -1), ...bridge, ...paperPoints];

  if (data.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-48 flex items-center justify-center">Sin datos de equity todavía.</div>;
  }

  const dateBySeq = new Map(data.map((p) => [p.seq, p.date]));

  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis
            dataKey="seq"
            type="number"
            domain={["dataMin", "dataMax"]}
            tick={{ fontSize: 10 }}
            minTickGap={40}
            tickFormatter={(seq: number) => dateBySeq.get(seq) ?? ""}
          />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={45} />
          <Tooltip
            formatter={(value, name) => [`${Number(value).toFixed(2)}%`, name === "historical" ? "Backtest" : "Paper trading"]}
            labelFormatter={(seq) => `Fecha: ${dateBySeq.get(Number(seq)) ?? seq}`}
          />
          <Line type="monotone" dataKey="historical" stroke="#2563eb" dot={false} strokeWidth={2} connectNulls={false} name="historical" />
          <Line type="monotone" dataKey="paper" stroke="#f59e0b" dot={{ r: 2 }} strokeWidth={1.5} connectNulls={false} name="paper" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
