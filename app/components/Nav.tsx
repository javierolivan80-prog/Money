import Link from "next/link";

// Nav.tsx — barra de navegación. Reestructurada a 5 pestañas en lenguaje
// llano (antes eran 7 con nombres técnicos en inglés: Overview, All Signals,
// Backtest Analysis, Calibration, Validation, Comparison, Signals This
// Week) — pedido explícito del usuario: menos pestañas, más fáciles de
// entender sin conocer la jerga del proyecto.
//
// Mapeo de dónde vivía cada cosa antes:
//   Overview                    -> Inicio (rehecha como semáforo)
//   All Signals                 -> Señales (misma página, reetiquetada)
//   Backtest Analysis + Signals
//     This Week                 -> Cartera (fusionadas: histórico + esta semana)
//   Calibration + Validation +
//     Comparison                -> ¿Funciona? (fusionadas: todo lo que
//                                  responde "¿me puedo fiar de esto?")
//   (nueva)                     -> Cómo funciona (explicación del motor,
//                                  sin datos — para quien no conoce el proyecto)
const TABS = [
  { href: "/", label: "Inicio" },
  { href: "/senales", label: "Señales" },
  { href: "/largo-plazo", label: "Largo plazo" },
  { href: "/cartera", label: "Cartera" },
  { href: "/funciona", label: "¿Funciona?" },
  { href: "/como-funciona", label: "Cómo funciona" },
] as const;

export function Nav({ active }: { active: string }) {
  return (
    <nav className="border-b border-neutral-200 dark:border-neutral-800 mb-6 -mx-6 px-6 overflow-x-auto">
      <div className="flex gap-1 max-w-7xl mx-auto">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${
              active === tab.href
                ? "border-neutral-900 dark:border-neutral-100 font-medium"
                : "border-transparent text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
            }`}
          >
            {tab.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
