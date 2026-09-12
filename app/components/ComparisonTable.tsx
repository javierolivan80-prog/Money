"use client";

// ComparisonTable.tsx — TAB 4 del spec: "Side-by-side comparison de las 3
// versiones". Primer uso de Tanstack Table en el proyecto (pedido
// explícitamente por el spec de la Fase 5) — para esta tabla en concreto
// (6 filas fijas, sin filtros) es más aparato del que hace falta, pero se
// usa aquí para tener un ejemplo mínimo antes de la tabla que sí lo
// necesita de verdad (signals feed, con sort/filtro/paginación reales).
import { flexRender, getCoreRowModel, useReactTable, createColumnHelper } from "@tanstack/react-table";

export interface ComparisonRow {
  metric: string;
  conservative: string;
  aggressive: string;
  balanced: string;
}

const columnHelper = createColumnHelper<ComparisonRow>();
const columns = [
  columnHelper.accessor("metric", { header: "Métrica" }),
  columnHelper.accessor("conservative", { header: "Conservative" }),
  columnHelper.accessor("aggressive", { header: "Aggressive" }),
  columnHelper.accessor("balanced", { header: "Balanced" }),
];

export function ComparisonTable({ rows }: { rows: ComparisonRow[] }) {
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <table className="w-full text-left border-collapse text-sm">
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id} className="border-b border-neutral-300 dark:border-neutral-700">
            {hg.headers.map((h) => (
              <th key={h.id} className="py-2 pr-4 font-medium">
                {flexRender(h.column.columnDef.header, h.getContext())}
              </th>
            ))}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id} className="border-b border-neutral-100 dark:border-neutral-900">
            {row.getVisibleCells().map((cell) => (
              <td key={cell.id} className="py-2 pr-4 font-mono">
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
