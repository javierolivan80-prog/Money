import type { Metadata } from "next";
import "./globals.css";

// Sin Google Fonts: una dependencia de red en build time es una fuente de
// fragilidad innecesaria para un dashboard de POC (mismo principio "lean"
// que el resto del proyecto — ver ARCHITECTURE_LEAN.md). Fuente del sistema.

export const metadata: Metadata = {
  title: "Money — Panel",
  description: "Panel de solo lectura: eventos detectados, análisis y resultados simulados",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body className="antialiased font-sans">{children}</body>
    </html>
  );
}
