"use client";

// ConfidenceBucketBars.tsx — TAB 2 "Win rate by confidence bucket" Y TAB 3
// (barras de fondo de la curva de calibración). Reutilizado en ambos tabs:
// mismo dato (ConfidenceBucket[]), dos contextos.
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ConfidenceBucket } from "@/lib/queries";

export function ConfidenceBucketBars({ buckets, label = "Win rate" }: { buckets: ConfidenceBucket[]; label?: string }) {
  if (buckets.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-40 flex items-center justify-center">Sin datos suficientes.</div>;
  }
  const data = buckets.map((b) => ({ ...b, hit_rate_pct: b.hit_rate * 100 }));

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={40} domain={[0, 100]} />
          <Tooltip
            formatter={(value, name, item) => [
              name === "hit_rate_pct" ? `${Number(value).toFixed(1)}% (n=${item.payload.n})` : value,
              label,
            ]}
          />
          <Bar dataKey="hit_rate_pct" fill="#2563eb">
            {data.map((d, i) => (
              <Cell key={i} fill={d.n < 5 ? "#93c5fd" : "#2563eb"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
