"use client";

// CalibrationCurve.tsx — TAB 3 del spec: "X: confidence predicted, Y: actual
// win rate, diagonal line (perfect calibration), your line (actual
// calibration)". Un punto por bucket de compute_calibration_diagnostics
// (portfolio_metrics.py), en el mismo formato estándar de un "reliability
// diagram" (Guo et al. 2017, la misma referencia que ya cita el docstring
// de compute_calibration_diagnostics para ECE).
import { CartesianGrid, Line, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import type { ConfidenceBucket } from "@/lib/queries";

export function CalibrationCurve({ buckets }: { buckets: ConfidenceBucket[] }) {
  if (buckets.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-56 flex items-center justify-center">Sin datos suficientes.</div>;
  }

  const points = buckets.map((b) => ({ confidence: b.mean_confidence, hit_rate: b.hit_rate * 100, n: b.n }));
  const diagonal = [
    { confidence: 0, hit_rate: 0 },
    { confidence: 100, hit_rate: 100 },
  ];

  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="confidence" type="number" domain={[0, 100]} unit="%" tick={{ fontSize: 10 }} name="Confidence" />
          <YAxis dataKey="hit_rate" type="number" domain={[0, 100]} unit="%" tick={{ fontSize: 10 }} width={40} name="Win rate real" />
          <ZAxis dataKey="n" range={[30, 300]} name="n" />
          <Tooltip
            formatter={(value, name) => [name === "n" ? value : `${Number(value).toFixed(1)}%`, name === "hit_rate" ? "Win rate real" : name === "confidence" ? "Confidence media" : "n"]}
          />
          <Line data={diagonal} dataKey="hit_rate" stroke="#9ca3af" strokeDasharray="4 3" dot={false} activeDot={false} legendType="none" />
          <Scatter data={points} fill="#2563eb" fillOpacity={0.7} line={{ stroke: "#2563eb", strokeWidth: 1.5 }} lineType="joint" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
