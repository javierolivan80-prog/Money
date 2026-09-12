"use client";

// ScatterPredictedActual.tsx — TAB 2 del spec: "Scatter: prediction (x) vs
// actual return (y)". La línea de referencia es la diagonal y=x (calibración
// perfecta), no una regresión OLS ajustada — R² ya viaje calculado en
// prediction_regression.r_squared (portfolio_metrics.compute_prediction_regression,
// correlación²) y se muestra como texto; dibujar la recta OLS exacta
// necesitaría pendiente/intercepto que ese cálculo no expone, y añadirlo
// solo para esta línea sería más ingeniería de la que pide el spec (que
// solo pide ver el R², no una ecuación).
import { CartesianGrid, Line, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis, ZAxis } from "recharts";

export function ScatterPredictedActual({ points, rSquared }: { points: { predicted: number; actual: number }[]; rSquared: number | null }) {
  if (points.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-48 flex items-center justify-center">Sin trades todavía.</div>;
  }

  const allValues = points.flatMap((p) => [p.predicted, p.actual]);
  const min = Math.min(...allValues, 0);
  const max = Math.max(...allValues, 0);
  const diagonal = [
    { predicted: min, actual: min },
    { predicted: max, actual: max },
  ];

  return (
    <div>
      <div className="h-48 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
            <XAxis
              dataKey="predicted"
              type="number"
              name="Predicho (EV%)"
              tick={{ fontSize: 10 }}
              unit="%"
              domain={[min, max]}
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <YAxis
              dataKey="actual"
              type="number"
              name="Real (%)"
              tick={{ fontSize: 10 }}
              unit="%"
              width={40}
              domain={[min, max]}
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <ZAxis range={[20, 20]} />
            <Tooltip
              formatter={(value) => `${Number(value).toFixed(2)}%`}
              cursor={{ strokeDasharray: "3 3" }}
            />
            <Line data={diagonal} dataKey="actual" stroke="#9ca3af" strokeDasharray="4 3" dot={false} activeDot={false} legendType="none" name="y=x" />
            <Scatter data={points} fill="#2563eb" fillOpacity={0.6} name="trades" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-neutral-500 mt-1">
        R² = {rSquared !== null ? rSquared.toFixed(3) : "—"} (línea gris = calibración perfecta, predicho == real)
      </p>
    </div>
  );
}
