"use client";

// ReturnHistogram.tsx — TAB 2 "Return distribution (histogram)". Bins
// calculados en el cliente (10 bins de ancho igual sobre el rango real de
// pnl_pct) — no hace falta tocar Python para esto, es una operación
// trivial sobre datos ya presentes en all_trades, a diferencia de las
// fórmulas financieras (Sharpe, calibración) que sí viven en Python.
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const N_BINS = 10;

export function ReturnHistogram({ pnlPcts }: { pnlPcts: number[] }) {
  if (pnlPcts.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-40 flex items-center justify-center">Sin trades todavía.</div>;
  }

  const min = Math.min(...pnlPcts);
  const max = Math.max(...pnlPcts);
  const range = max - min || 1;
  const binWidth = range / N_BINS;

  const bins = Array.from({ length: N_BINS }, (_, i) => {
    const lo = min + i * binWidth;
    const hi = lo + binWidth;
    return { lo, hi, label: `${lo.toFixed(1)}`, count: 0 };
  });
  for (const p of pnlPcts) {
    const idx = Math.min(N_BINS - 1, Math.floor((p - min) / binWidth));
    bins[idx].count += 1;
  }

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={bins} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="label" tick={{ fontSize: 9 }} unit="%" />
          <YAxis tick={{ fontSize: 10 }} width={30} allowDecimals={false} />
          <Tooltip
            formatter={(value, _name, item) => [`${value} trades`, `${item.payload.lo.toFixed(1)}% a ${item.payload.hi.toFixed(1)}%`]}
          />
          <Bar dataKey="count">
            {bins.map((b, i) => (
              <Cell key={i} fill={b.hi <= 0 ? "#dc2626" : b.lo >= 0 ? "#16a34a" : "#9ca3af"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
