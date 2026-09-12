"use client";

// DailyPnLChart.tsx — TAB 5 "Daily P&L chart (bar)". Agrega
// last_10_closed_trades por exit_date — para una sola semana de paper
// trading esto cubre prácticamente todos los cierres (el reporte no expone
// más de los últimos 10, documentado en paper_trading/report.py; con el
// volumen de una semana de POC, 10 alcanza casi siempre).
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PaperClosedTrade } from "@/lib/queries";

export function DailyPnLChart({ trades }: { trades: PaperClosedTrade[] }) {
  if (trades.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-40 flex items-center justify-center">Sin trades cerrados esta semana.</div>;
  }

  const byDate = new Map<string, number>();
  for (const t of trades) {
    byDate.set(t.exit_date, (byDate.get(t.exit_date) ?? 0) + t.pnl_pct);
  }
  const data = Array.from(byDate.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, pnl]) => ({ date, pnl }));

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={40} />
          <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} />
          <Bar dataKey="pnl">
            {data.map((d, i) => (
              <Cell key={i} fill={d.pnl >= 0 ? "#16a34a" : "#dc2626"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
