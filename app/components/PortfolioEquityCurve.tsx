// PortfolioEquityCurve.tsx — mismo patrón de SVG a mano que EquityCurve.tsx
// (sin librería de charting, ver su comentario), pero sobre balance en
// dólares en vez de retorno acumulado en % — son escalas distintas
// (portfolio_equity_curve.balance parte de starting_capital, no de 0), así
// que no comparten componente: forzar una unión hubiera significado una
// prop de "modo" innecesaria para dos usos que no cambian de forma.
import type { PortfolioEquityPoint } from "@/lib/queries";

export function PortfolioEquityCurve({ points, startingCapital }: { points: PortfolioEquityPoint[]; startingCapital: number }) {
  if (points.length === 0) {
    return <div className="text-sm text-neutral-500 italic">Sin curva de equity todavía.</div>;
  }

  const width = 320;
  const height = 120;
  const padding = 8;

  const values = points.map((p) => p.balance);
  const min = Math.min(startingCapital, ...values);
  const max = Math.max(startingCapital, ...values);
  const range = max - min || 1;

  const toXY = (i: number, v: number): [number, number] => {
    const x = padding + (i / Math.max(points.length - 1, 1)) * (width - 2 * padding);
    const y = height - padding - ((v - min) / range) * (height - 2 * padding);
    return [x, y];
  };

  const pathD = points
    .map((p, i) => {
      const [x, y] = toXY(i, p.balance);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const [baseX1, baseY] = toXY(0, startingCapital);
  const [baseX2] = toXY(points.length - 1, startingCapital);
  const last = values[values.length - 1];
  const strokeColor = last >= startingCapital ? "#16a34a" : "#dc2626";

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" role="img" aria-label="Curva de equity en dólares">
      <line x1={baseX1} y1={baseY} x2={baseX2} y2={baseY} stroke="currentColor" strokeOpacity={0.2} strokeDasharray="4 3" />
      <path d={pathD} fill="none" stroke={strokeColor} strokeWidth={1.5} />
    </svg>
  );
}
