// db.ts — pool de Postgres para el dashboard.
//
// Sin ORM (mismo principio que el pipeline de Python: ARCHITECTURE_LEAN.md
// §2, "sin ORM"). SQL explícito con el driver `pg`.
//
// El dashboard es de SOLO LECTURA. Nunca escribe en la base de datos — toda
// la escritura ocurre en el pipeline de Python (GitHub Actions), que es el
// único proceso con salida de red hacia EDGAR/FDA/yfinance (ver
// AUDIT_LEAN.md §1.5 y RUNBOOK.md). El dashboard solo lee lo que el pipeline
// ya dejó en Postgres.
import { Pool } from "pg";

let pool: Pool | null = null;

export function getPool(): Pool {
  if (!process.env.DATABASE_URL) {
    throw new Error(
      "DATABASE_URL no está definida. Ver RUNBOOK.md para cómo configurarla " +
        "(Postgres alojado en Neon/Supabase, la misma URL que usa el pipeline)."
    );
  }
  if (!pool) {
    pool = new Pool({
      // trim(): el mismo fallo que ya tumbó el pipeline dos veces (ver
      // pipeline/config.py) — un secreto pegado con un salto de línea final.
      connectionString: process.env.DATABASE_URL.trim(),
      // En Vercel cada instancia serverless abre su propio pool; sin tope,
      // unas pocas visitas simultáneas agotan las conexiones del plan
      // gratuito de Neon/Supabase. Las páginas hacen pocas consultas cada una.
      max: 3,
      idleTimeoutMillis: 10_000,
    });
  }
  return pool;
}

export function isDatabaseConfigured(): boolean {
  return Boolean(process.env.DATABASE_URL);
}
