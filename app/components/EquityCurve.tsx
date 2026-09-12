// EquityCurve.tsx — SVG simple, sin librería de gráficos.
//
// Decisión deliberada: para un POC de una semana, una dependencia de
// charting (recharts, visx, ...) es peso muerto. Un polyline SVG a mano
// cubre exactamente lo que hace falta (ver una curva de equity) sin
// arrastrar una librería entera para eso.
import type { EquityPoint } from "@/lib/queries";

export function EquityCurve({ points }: { points: EquityPoint[] }) {
  if (points.length === 0) {
    return <div className="text-sm text-neutral-500 italic">Sin trades cerrados todavía.</div>;
  }

  const width = 320;
  const height = 120;
  const padding = 8;

  const values = points.map((p) => p.cumulative_return_pct);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const range = max - min || 1;

  const toXY = (i: number, v: number): [number, number] => {
    const x = padding + (i / Math.max(points.length - 1, 1)) * (width - 2 * padding);
    const y = height - padding - ((v - min) / range) * (height - 2 * padding);
    return [x, y];
  };

  const pathD = points
    .map((p, i) => {
      const [x, y] = toXY(i, p.cumulative_return_pct);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const [zeroX1, zeroY] = toXY(0, 0);
  const [zeroX2] = toXY(points.length - 1, 0);
  const last = values[values.length - 1];
  const strokeColor = last >= 0 ? "#16a34a" : "#dc2626";

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" role="img" aria-label="Curva de equity acumulada">
      <line x1={zeroX1} y1={zeroY} x2={zeroX2} y2={zeroY} stroke="currentColor" strokeOpacity={0.2} strokeDasharray="4 3" />
      <path d={pathD} fill="none" stroke={strokeColor} strokeWidth={1.5} />
    </svg>
  );
}
