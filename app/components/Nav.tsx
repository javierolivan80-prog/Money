import Link from "next/link";

// Nav.tsx — barra de navegación compartida entre las 6 vistas de la Fase 5
// (spec: "Dashboard con 3 columnas paralelas" + 5 tabs). Server component
// simple, sin estado de cliente — el tab activo se resalta con el pathname
// que cada page.tsx conoce (usePathname exigiría "use client" en todas las
// páginas solo para esto; en vez de eso, cada página pasa su propio
// `active`).
const TABS = [
  { href: "/", label: "Overview" },
  { href: "/signals", label: "All Signals" },
  { href: "/portfolio", label: "Backtest Analysis" },
  { href: "/calibration", label: "Calibration" },
  { href: "/validation", label: "Validation" },
  { href: "/comparison", label: "Comparison" },
  { href: "/week", label: "Signals This Week" },
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
