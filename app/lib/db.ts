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
    pool = new Pool({ connectionString: process.env.DATABASE_URL });
  }
  return pool;
}

export function isDatabaseConfigured(): boolean {
  return Boolean(process.env.DATABASE_URL);
}
