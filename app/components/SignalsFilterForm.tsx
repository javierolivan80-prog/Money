"use client";

// SignalsFilterForm.tsx — TAB 1 "Filters: ticker, event_type, signal, date
// range, min_confidence". El filtrado real ocurre en el SERVIDOR (SQL en
// getSignalsFeed) — este formulario solo actualiza los searchParams de la
// URL y deja que page.tsx (server component) vuelva a pedir los datos ya
// filtrados; SignalsTable (Tanstack Table) solo ordena/pagina lo que ya
// llegó filtrado, no re-filtra en el cliente.
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

export function SignalsFilterForm({ eventClasses }: { eventClasses: string[] }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [ticker, setTicker] = useState(searchParams.get("ticker") ?? "");
  const [eventClass, setEventClass] = useState(searchParams.get("eventClass") ?? "");
  const [signal, setSignal] = useState(searchParams.get("signal") ?? "");
  const [dateFrom, setDateFrom] = useState(searchParams.get("dateFrom") ?? "");
  const [dateTo, setDateTo] = useState(searchParams.get("dateTo") ?? "");
  const [minConfidence, setMinConfidence] = useState(searchParams.get("minConfidence") ?? "");

  function apply(e: React.FormEvent) {
    e.preventDefault();
    const params = new URLSearchParams();
    if (ticker) params.set("ticker", ticker);
    if (eventClass) params.set("eventClass", eventClass);
    if (signal) params.set("signal", signal);
    if (dateFrom) params.set("dateFrom", dateFrom);
    if (dateTo) params.set("dateTo", dateTo);
    if (minConfidence) params.set("minConfidence", minConfidence);
    router.push(`/senales?${params.toString()}`);
  }

  function clear() {
    setTicker("");
    setEventClass("");
    setSignal("");
    setDateFrom("");
    setDateTo("");
    setMinConfidence("");
    router.push("/senales");
  }

  const inputClass = "border border-neutral-300 dark:border-neutral-700 bg-transparent rounded px-2 py-1 text-sm";

  return (
    <form onSubmit={apply} className="flex flex-wrap gap-2 items-end mb-4 text-sm">
      <div className="flex flex-col">
        <label className="text-xs text-neutral-500">Ticker</label>
        <input className={inputClass} value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL" />
      </div>
      <div className="flex flex-col">
        <label className="text-xs text-neutral-500">Event type</label>
        <select className={inputClass} value={eventClass} onChange={(e) => setEventClass(e.target.value)}>
          <option value="">Todos</option>
          {eventClasses.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col">
        <label className="text-xs text-neutral-500">Signal</label>
        <select className={inputClass} value={signal} onChange={(e) => setSignal(e.target.value)}>
          <option value="">Todos</option>
          <option value="LONG">LONG</option>
          <option value="SHORT">SHORT</option>
          <option value="NO_TRADE">NO_TRADE</option>
        </select>
      </div>
      <div className="flex flex-col">
        <label className="text-xs text-neutral-500">Desde</label>
        <input type="date" className={inputClass} value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
      </div>
      <div className="flex flex-col">
        <label className="text-xs text-neutral-500">Hasta</label>
        <input type="date" className={inputClass} value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
      </div>
      <div className="flex flex-col">
        <label className="text-xs text-neutral-500">Min. confidence</label>
        <input type="number" min={0} max={100} className={`${inputClass} w-20`} value={minConfidence} onChange={(e) => setMinConfidence(e.target.value)} />
      </div>
      <button type="submit" className="border border-neutral-900 dark:border-neutral-100 rounded px-3 py-1">
        Filtrar
      </button>
      <button type="button" onClick={clear} className="text-neutral-500 underline px-1">
        Limpiar
      </button>
    </form>
  );
}
