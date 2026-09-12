import { isDatabaseConfigured } from "@/lib/db";
import { getEventClasses, getSignalsFeed } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { SignalsFilterForm } from "@/components/SignalsFilterForm";
import { SignalsTable } from "@/components/SignalsTable";

export const dynamic = "force-dynamic";

export default async function SignalsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/signals" />
        <h1 className="text-2xl font-bold mb-4">All Signals</h1>
        <p className="text-sm text-neutral-500">DATABASE_URL no está configurada.</p>
      </main>
    );
  }

  const params = await searchParams;
  const [eventClasses, rows] = await Promise.all([
    getEventClasses(),
    getSignalsFeed({
      ticker: params.ticker,
      eventClass: params.eventClass,
      signal: params.signal as "LONG" | "SHORT" | "NO_TRADE" | undefined,
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      minConfidence: params.minConfidence ? Number(params.minConfidence) : undefined,
      limit: 500,
    }),
  ]);

  return (
    <main className="max-w-7xl mx-auto p-6">
      <Nav active="/signals" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">All Signals</h1>
        <p className="text-sm text-neutral-500 mt-1">
          Feed cronológico de todos los eventos analizados (con o sin trade_decision) — máx. 500 más recientes que
          cumplan los filtros.
        </p>
      </header>

      <SignalsFilterForm eventClasses={eventClasses} />

      {rows.length === 0 ? (
        <p className="text-sm text-neutral-500 italic">Sin eventos que cumplan estos filtros.</p>
      ) : (
        <SignalsTable rows={rows} />
      )}
    </main>
  );
}
