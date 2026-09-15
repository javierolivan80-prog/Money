"use client";

// SignalsTable.tsx — TAB 1 "All Signals": feed cronológico con
// sort/paginación real vía Tanstack Table (el filtrado ya llegó hecho del
// servidor, ver SignalsFilterForm). Bull/Bear/Judge se expande por fila
// (colapsable, spec: "collapsible") — un Set de event_ids expandidos en
// vez del expanding model de Tanstack, más simple para una sub-fila de
// texto libre en vez de una tabla anidada.
import { Fragment, useState } from "react";
import { createColumnHelper, flexRender, getCoreRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type SortingState } from "@tanstack/react-table";
import type { SignalFeedRow } from "@/lib/queries";
import { SignalDetail } from "@/components/SignalDetail";

const SIGNAL_COLORS: Record<string, string> = {
  LONG: "text-green-600 dark:text-green-400",
  SHORT: "text-red-600 dark:text-red-400",
  NO_TRADE: "text-neutral-400",
};

const columnHelper = createColumnHelper<SignalFeedRow>();

const columns = [
  columnHelper.accessor("d0_close_date", { header: "Fecha", cell: (c) => c.getValue() }),
  columnHelper.accessor("ticker", { header: "Ticker", cell: (c) => <span className="font-mono">{c.getValue()}</span> }),
  columnHelper.accessor("event_class", { header: "Evento", cell: (c) => c.getValue().replace(/^8K_/, "") }),
  columnHelper.accessor("source", { header: "Fuente" }),
  columnHelper.accessor("novelty_score", {
    header: "Sorpresa",
    cell: (c) => (
      <div className="flex items-center gap-1 w-20">
        <div className="flex-1 bg-neutral-200 dark:bg-neutral-800 rounded h-1.5">
          <div className="bg-purple-500 h-1.5 rounded" style={{ width: `${c.getValue()}%` }} />
        </div>
        <span className="text-xs">{c.getValue()}</span>
      </div>
    ),
  }),
  columnHelper.accessor("signal", {
    header: "Decisión",
    cell: (c) => <span className={`font-medium ${SIGNAL_COLORS[c.getValue()]}`}>{c.getValue()}</span>,
  }),
  columnHelper.accessor("confidence", { header: "Confianza", cell: (c) => `${c.getValue().toFixed(0)}%` }),
  columnHelper.accessor("ev_balanced", { header: "Valor esperado", cell: (c) => `${(c.getValue() * 100).toFixed(2)}%` }),
  columnHelper.accessor("pnl_pct", {
    header: "Resultado (si se operó)",
    cell: (c) => {
      const v = c.getValue();
      if (v === null) return "—";
      return <span className={v >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}>{v.toFixed(2)}%</span>;
    },
  }),
];

export function SignalsTable({ rows }: { rows: SignalFeedRow[] }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "d0_close_date", desc: true }]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  });

  function toggle(eventId: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(eventId)) next.delete(eventId);
      else next.add(eventId);
      return next;
    });
  }

  return (
    <div>
      <table className="w-full text-left border-collapse text-sm">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-neutral-300 dark:border-neutral-700">
              <th className="py-2 pr-2 w-6"></th>
              {hg.headers.map((h) => (
                <th key={h.id} className="py-2 pr-4 font-medium cursor-pointer select-none" onClick={h.column.getToggleSortingHandler()}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {{ asc: " ▲", desc: " ▼" }[h.column.getIsSorted() as string] ?? ""}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const isExpanded = expanded.has(row.original.event_id);
            return (
              <Fragment key={row.id}>
                <tr className="border-b border-neutral-100 dark:border-neutral-900">
                  <td className="py-2 pr-2">
                    <button onClick={() => toggle(row.original.event_id)} className="text-neutral-400 hover:text-neutral-900 dark:hover:text-neutral-100">
                      {isExpanded ? "▾" : "▸"}
                    </button>
                  </td>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="py-2 pr-4">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
                {isExpanded && <SignalDetail row={row.original} colSpan={columns.length + 1} />}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      <div className="flex items-center gap-2 mt-3 text-sm">
        <button
          onClick={() => table.previousPage()}
          disabled={!table.getCanPreviousPage()}
          className="border border-neutral-300 dark:border-neutral-700 rounded px-2 py-1 disabled:opacity-40"
        >
          ← Anterior
        </button>
        <span className="text-neutral-500">
          Página {table.getState().pagination.pageIndex + 1} de {table.getPageCount() || 1} ({rows.length} eventos)
        </span>
        <button
          onClick={() => table.nextPage()}
          disabled={!table.getCanNextPage()}
          className="border border-neutral-300 dark:border-neutral-700 rounded px-2 py-1 disabled:opacity-40"
        >
          Siguiente →
        </button>
      </div>
    </div>
  );
}
