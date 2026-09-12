"use client";

// ExportPdfButton.tsx — spec Fase 5: "Botón 'Download backtest report' → PDF
// con summary metrics, equity curves, top trades, event study by class,
// calibration analysis, recomendación". Genera un PDF real en el cliente
// (jsPDF + jspdf-autotable) a partir de los mismos datos ya en pantalla —
// sin capturar las curvas de equity como imagen (html2canvas sobre SVGs
// dinámicos es frágil: fuentes, tainted canvas, tamaño variable): las
// curvas se representan como su TABLA de puntos clave (balance inicial,
// final, pico, valle) en vez de un gráfico — sigue siendo "equity curves"
// en el sentido de datos, no rendering. Documentado aquí, no fingido.
import jsPDF from "jspdf";
import autoTable from "jspdf-autotable";
import type { PortfolioReport, StrategyVersion } from "@/lib/queries";

const VERSIONS: StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

export function ExportPdfButton({ report }: { report: PortfolioReport }) {
  function handleExport() {
    const doc = new jsPDF();
    let y = 15;

    doc.setFontSize(16);
    doc.text("Money POC — Backtest Report", 14, y);
    y += 7;
    doc.setFontSize(10);
    doc.setTextColor(100);
    doc.text(`Corrida: ${report.run_batch_tag} · Capital inicial: $${report.starting_capital.toLocaleString("en-US")}`, 14, y);
    y += 5;
    doc.text(`Generado: ${new Date().toISOString().slice(0, 19).replace("T", " ")}`, 14, y);
    y += 8;

    doc.setTextColor(0);
    doc.setFontSize(12);
    doc.text(`Recomendación: ${report.recommendation.verdict}`, 14, y);
    y += 6;
    doc.setFontSize(9);
    for (const finding of report.recommendation.findings) {
      const lines = doc.splitTextToSize(`• ${finding}`, 180);
      doc.text(lines, 14, y);
      y += lines.length * 4;
    }
    y += 4;

    autoTable(doc, {
      startY: y,
      head: [["Métrica", "Conservative", "Balanced", "Aggressive"]],
      body: [
        ["Trades", ...VERSIONS.map((v) => String(report.versions[v].trade_metrics.total_trades))],
        ["Win rate", ...VERSIONS.map((v) => fmtPct(report.versions[v].trade_metrics.win_rate))],
        ["Total return", ...VERSIONS.map((v) => fmtPct(report.versions[v].equity_metrics.total_return))],
        ["Sharpe", ...VERSIONS.map((v) => fmtNum(report.versions[v].equity_metrics.sharpe_ratio))],
        ["Max drawdown", ...VERSIONS.map((v) => fmtPct(report.versions[v].equity_metrics.max_drawdown))],
        ["Calibración (Fase 3)", ...VERSIONS.map((v) => fmtNum(report.versions[v].calibration.calibration_score))],
        ["Calibración (correl.)", ...VERSIONS.map((v) => fmtNum(report.versions[v].confidence_calibration.correlation))],
      ],
      styles: { fontSize: 8 },
      headStyles: { fillColor: [30, 41, 59] },
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    y = (doc as any).lastAutoTable.finalY + 8;

    for (const version of VERSIONS) {
      const v = report.versions[version];
      if (y > 250) {
        doc.addPage();
        y = 15;
      }
      doc.setFontSize(11);
      doc.text(`${version} — curva de equity (puntos clave)`, 14, y);
      y += 2;
      const curve = v.equity_curve;
      const balances = curve.map((p) => p.balance);
      const peak = balances.length > 0 ? Math.max(...balances) : null;
      const trough = balances.length > 0 ? Math.min(...balances) : null;
      autoTable(doc, {
        startY: y + 2,
        head: [["Balance inicial", "Balance final", "Pico", "Valle"]],
        body: [[
          fmtDollars(balances[0] ?? null),
          fmtDollars(v.equity_metrics.final_balance),
          fmtDollars(peak),
          fmtDollars(trough),
        ]],
        styles: { fontSize: 8 },
        headStyles: { fillColor: [30, 41, 59] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      doc.setFontSize(9);
      doc.text(`Top 5 ganadores (${version})`, 14, y);
      y += 2;
      autoTable(doc, {
        startY: y + 2,
        head: [["Ticker", "Clase", "Salida", "PnL %"]],
        body: v.top_10_winners.slice(0, 5).map((t) => [t.ticker ?? "—", (t.event_class ?? "").replace(/^8K_/, ""), t.exit_reason, `${t.pnl_pct.toFixed(2)}%`]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [22, 101, 52] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      doc.text(`Top 5 perdedores (${version})`, 14, y);
      y += 2;
      autoTable(doc, {
        startY: y + 2,
        head: [["Ticker", "Clase", "Salida", "PnL %"]],
        body: v.top_10_losers.slice(0, 5).map((t) => [t.ticker ?? "—", (t.event_class ?? "").replace(/^8K_/, ""), t.exit_reason, `${t.pnl_pct.toFixed(2)}%`]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [153, 27, 27] },
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      y = (doc as any).lastAutoTable.finalY + 4;

      const eventTypeRows = Object.values(v.metrics_by_event_type);
      if (eventTypeRows.length > 0) {
        doc.text(`Event study por clase (${version})`, 14, y);
        y += 2;
        autoTable(doc, {
          startY: y + 2,
          head: [["Clase", "N", "Win rate", "Retorno medio", "n<20"]],
          body: eventTypeRows.map((r) => [
            r.event_type.replace(/^8K_/, ""),
            String(r.n_trades),
            fmtPct(r.win_rate),
            `${r.avg_return.toFixed(2)}%`,
            r.insufficient_sample ? "sí" : "no",
          ]),
          styles: { fontSize: 8 },
          headStyles: { fillColor: [30, 41, 59] },
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        y = (doc as any).lastAutoTable.finalY + 8;
      }
    }

    doc.save(`money-poc-backtest-${report.run_batch_tag}.pdf`);
  }

  return (
    <button
      onClick={handleExport}
      className="border border-neutral-900 dark:border-neutral-100 rounded px-3 py-1.5 text-sm hover:bg-neutral-900 hover:text-white dark:hover:bg-neutral-100 dark:hover:text-neutral-900 transition-colors"
    >
      Download backtest report (PDF)
    </button>
  );
}

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}
function fmtNum(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}
function fmtDollars(v: number | null): string {
  return v === null ? "—" : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}
