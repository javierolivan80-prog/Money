"use client";

// DrawdownChart.tsx — TAB 2 "Drawdown chart (underwater plot)". drawdown(t)
// = (balance(t) - peak_hasta_t) / peak_hasta_t, derivado en el cliente de
// equity_curve — es aritmética directa (un running max), no una fórmula
// financiera que deba vivir en Python (a diferencia de max_drawdown, que sí
// es un resumen agregado ya calculado por compute_equity_metrics; esto es
// la SERIE completa punto a punto, que ese resumen no expone).
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PortfolioEquityPoint } from "@/lib/queries";

export function DrawdownChart({ equityCurve }: { equityCurve: PortfolioEquityPoint[] }) {
  if (equityCurve.length === 0) {
    return <div className="text-sm text-neutral-500 italic h-40 flex items-center justify-center">Sin datos todavía.</div>;
  }

  let peak = equityCurve[0].balance;
  const data = equityCurve.map((p, i) => {
    peak = Math.max(peak, p.balance);
    return { seq: i, date: p.trade_date, drawdown: peak > 0 ? ((p.balance - peak) / peak) * 100 : 0 };
  });
  const dateBySeq = new Map(data.map((d) => [d.seq, d.date]));

  return (
    <div className="h-40 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
          <CartesianGrid strokeDasharray="3 3" opacity={0.2} />
          <XAxis dataKey="seq" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} tickFormatter={(seq: number) => dateBySeq.get(seq) ?? ""} minTickGap={40} />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={40} />
          <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} labelFormatter={(seq) => `Fecha: ${dateBySeq.get(Number(seq)) ?? seq}`} />
          <Area type="monotone" dataKey="drawdown" stroke="#dc2626" fill="#dc2626" fillOpacity={0.2} strokeWidth={1.5} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
