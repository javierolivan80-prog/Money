import { isDatabaseConfigured } from "@/lib/db";
import { getEventClasses, getSignalsFeed } from "@/lib/queries";
import { Nav } from "@/components/Nav";
import { SignalsFilterForm } from "@/components/SignalsFilterForm";
import { SignalsTable } from "@/components/SignalsTable";

export const dynamic = "force-dynamic";

export default async function SenalesPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  if (!isDatabaseConfigured()) {
    return (
      <main className="max-w-3xl mx-auto p-8">
        <Nav active="/senales" />
        <h1 className="text-2xl font-bold mb-4">Señales</h1>
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
      <Nav active="/senales" />
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Señales</h1>
        <p className="text-sm text-neutral-500 mt-1">
          Cada evento analizado, más reciente primero — abre una fila (▸) para ver el razonamiento completo: quién opina qué, cuántos
          casos parecidos hubo antes, y por qué se opera o no. Máx. 500.
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
