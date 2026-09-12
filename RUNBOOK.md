# RUNBOOK.md — cómo poner esto a correr

Este documento es el puente entre lo que se construyó en esta sesión y lo que
tiene que pasar en tu infraestructura para que corra de verdad. Léelo antes de
tocar nada — el orden importa.

## 0. Por qué existe este documento (léelo primero)

Esta sesión de desarrollo corre en un sandbox de Claude Code cuya política de
red bloquea la salida a **cualquier host de scraping externo**: SEC EDGAR,
openFDA, Yahoo Finance, e incluso la Ken French Data Library (Dartmouth). Lo
único que sí funciona desde aquí: PyPI, npm, GitHub, y la API de Anthropic
(sin clave configurada, tampoco se pudo probar en vivo). Ver `AUDIT_LEAN.md`
§1.5 para el detalle de qué se verificó y cómo.

**Consecuencia de diseño, no un parche:** el pipeline de datos vive en
**GitHub Actions** (`.github/workflows/nightly_pipeline.yml`), no en ningún
sandbox de desarrollo ni en un servidor propio. Los runners de GitHub Actions
tienen salida a internet completa. Esto además encaja con el diseño: todo el
sistema es un batch nocturno (`ARCHITECTURE_LEAN.md` §2.5), así que un cron de
Actions es toda la orquestación que hace falta.

**Qué SÍ se validó en esta sesión, y cómo** (para que sepas qué confianza dar
a cada pieza):
- Los 39 tests de `pipeline/tests/` pasan, incluyendo contra un **Postgres 16
  real** levantado en este sandbox (no un mock) — el schema, los upserts
  idempotentes, los CHECK constraints anti-look-ahead, y la detección de gaps
  de supervivencia se probaron con SQL real ejecutándose de verdad.
- El motor de backtest pasó **T1 (placebo)** y una réplica sintética de
  **T2** con datos generados, no reales — ver `ARCHITECTURE_LEAN.md` §8 para
  qué significa cada uno.
- El dashboard de Next.js compila, y se probó en runtime contra ese mismo
  Postgres real: el estado vacío, el estado "sin runs todavía", y el
  renderizado completo con 30 trades sintéticos (equity curve SVG, tabla,
  métricas) — esto encontró y corrigió un bug real (node-postgres devuelve
  columnas NUMERIC como string, no number).
- **Lo que NO se validó, porque no hay red hacia esos hosts desde aquí:** el
  scraper de EDGAR, el backfill de yfinance, y el fetcher de Ken French nunca
  se han ejecutado contra los servidores reales. Siguen el formato público
  documentado, pero cada uno lleva una advertencia en su docstring señalando
  exactamente qué verificar a mano antes de confiar en un backfill completo.

## 1. Qué necesitas crear (cuentas gratuitas)

| Servicio | Para qué | Plan |
|---|---|---|
| [Neon](https://neon.tech) o [Supabase](https://supabase.com) | Postgres alojado | Free tier — de sobra para este POC |
| [console.anthropic.com](https://console.anthropic.com) | API key para el analizador Bull/Bear/Judge | Pago por uso — ver `ARCHITECTURE_LEAN.md` §6 para el coste estimado (~$50-100 el backfill completo) |
| [Vercel](https://vercel.com) | Hosting del dashboard Next.js | Free tier |
| GitHub (ya lo tienes) | Ejecuta el pipeline nocturno | Actions incluido en el free tier para repos públicos; con límite de minutos en privados |

## 2. Configurar los secrets de GitHub Actions

En el repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|---|---|
| `DATABASE_URL` | La cadena de conexión de Neon/Supabase (incluye usuario/contraseña) |
| `ANTHROPIC_API_KEY` | Tu clave de console.anthropic.com |
| `EDGAR_USER_AGENT` | `"Tu Nombre tu@email.com"` — la SEC exige un contacto real, o bloquea con 403 |

## 3. Primer arranque (en este orden — no te lo saltes)

### 3.1 Aplicar el schema
Se aplica solo con el primer run del workflow (`init_schema` está en el job
`ingest_and_analyze`), pero si quieres verificarlo a mano primero:

```bash
export DATABASE_URL="tu-cadena-de-neon-o-supabase"
pip install -r pipeline/requirements.txt
python -c "from pipeline.db.connection import get_connection, init_schema; init_schema(get_connection())"
```

### 3.2 Verificar EDGAR con UN día antes del backfill completo
El scraper nunca se ha ejecutado contra `www.sec.gov` real (sin red desde el
sandbox de desarrollo). Antes de lanzar 5 años:

```bash
export EDGAR_USER_AGENT="Tu Nombre tu@email.com"
python -m pipeline.ingest.edgar_scraper --single-day 2024-03-15
```

Revisa la salida a mano: ¿los nombres de empresa y los Items parecen
correctos? Si `fetch_filing_item_codes` devuelve listas vacías de items para
filings que sabes que traen 2.02, la cabecera SGML puede tener un formato
distinto al asumido en `ITEM_TITLE_TO_NUMBER` — ver la advertencia en el
docstring de `pipeline/ingest/edgar_scraper.py`.

### 3.3 Lanzar el backfill completo de EDGAR (5 años)
Vía GitHub Actions: pestaña **Actions → Nightly Pipeline → Run workflow**,
dejando `single_day` vacío para el flujo incremental normal, o lanzando el
scraper directamente por rango si prefieres correrlo tú:

```bash
python -m pipeline.ingest.edgar_scraper --start 2021-01-01 --end 2025-12-31
```

Esto puede tardar horas por el rate limit de la SEC (8 req/s). Es resumible:
si se corta, vuelve a lanzar — el `UNIQUE` constraint hace que no duplique
nada ya insertado.

### 3.4 Backfill de precios (yfinance) — el paso más frágil
**Lánzalo en paralelo al resto, el primer día, no lo dejes para el final**
(`ARCHITECTURE_LEAN.md` §7: es la dependencia crítica del plan de 7 días).

Vía Actions: **Run workflow** con el job `price_backfill_manual` (se activa
solo en `workflow_dispatch`). O a mano:

```bash
python -c "
from pipeline.db.connection import get_connection
conn = get_connection()
with conn.cursor() as cur:
    cur.execute(\"SELECT DISTINCT ticker FROM universe WHERE ticker IS NOT NULL\")
    tickers = [r['ticker'] for r in cur.fetchall()]
print('\n'.join(tickers))
" > /tmp/tickers.txt

python -m pipeline.ingest.yfinance_backfill --tickers /tmp/tickers.txt
```

yfinance no es una API oficial: espera 429s alrededor de los ~950 tickers
seguidos. El script ya tiene backoff y lotes de 50 con pausa — si aun así se
cae, es resumible por lotes (revisa qué tickers ya tienen filas en `prices`
antes de relanzar, para no perder tiempo redescargando).

**Sobre los WARNING de supervivencia**: es esperado ver filas
`survivorship_warning = TRUE`. NO intentes llenarlas ni interpolarlas — es
información, no un bug (instrucción explícita, ver docstring del script).

### 3.5 Factores Fama-French

```bash
python -m pipeline.ingest.fama_french
```

### 3.6 Smoke test del analizador Bull/Bear/Judge
**Nunca se ha llamado a la API de Anthropic en vivo desde esta sesión** (sin
`ANTHROPIC_API_KEY` configurada aquí). Antes del backfill completo de ~25k
eventos:

```bash
export ANTHROPIC_API_KEY="tu-clave"
python -m pipeline.analyze.adversarial_analyzer --smoke-test 5
```

Revisa a mano las 5 tesis Bull/Bear/Judge generadas en la tabla `analyses`.
Si el JSON no parsea o los campos no tienen sentido, es más barato
descubrirlo en 5 eventos que en 25.000 ($0.02 vs. ~$50-100 —
`ARCHITECTURE_LEAN.md` §6).

## 4. Desplegar el dashboard

```bash
cd app
vercel  # sigue el flujo interactivo, o usa la integración de GitHub de Vercel
```

En Vercel: **Project Settings → Environment Variables** → añade `DATABASE_URL`
con la misma cadena que usa el pipeline. El dashboard es de **solo lectura** —
nunca escribe en la base de datos, así que no necesita `ANTHROPIC_API_KEY` ni
`EDGAR_USER_AGENT`.

Para desarrollo local: `cp app/.env.local.example app/.env.local`, edita la
URL, y `cd app && npm install && npm run dev`.

## 5. Orden de ejecución de los 7 días (resumen — detalle en ARCHITECTURE_LEAN.md §9)

| Día | Qué hacer | Bloqueante |
|---|---|---|
| 1 | Provisionar Neon/Supabase, configurar secrets, `--single-day` de EDGAR, **lanzar backfill de yfinance en background** | El backfill de precios es la ruta crítica |
| 2 | Backfill completo de EDGAR, ingesta FDA/RSS (no incluida en este commit — ver §6 de este documento) | |
| 3 | Factores FF3, verificar `universe` con deslistados | |
| 4 | Correr `compute_car` sobre eventos reales | |
| 5 | **T1 y T2 con datos reales** (aquí solo se validaron con datos sintéticos) | Bloqueante — no seguir sin esto |
| 6 | Pre-registro commiteado → backtest in-sample → una sola pasada OOS | |
| 7 | Dashboard con datos reales, conclusiones | |

## 6. Lo que este commit NO incluye (y por qué)

- **Ingesta de FDA (openFDA + RSS)**: el spec de esta ronda pidió "EDGAR es el
  eje: earnings + 8-K. FDA es satélite exploratorio" — se priorizó construir
  y validar bien la pata EDGAR (que es donde vive la potencia estadística,
  `AUDIT_LEAN.md` §2.2.3) antes que dispersar el esfuerzo. Añadir
  `pipeline/ingest/fda_scraper.py` siguiendo el mismo patrón de
  `edgar_scraper.py` es directo cuando llegue el momento.
- **Extracción del texto real del filing**: `edgar_scraper.py` guarda la URL
  y los metadatos del filing, pero `adversarial_analyzer.py` todavía recibe
  un placeholder de texto (`filing_excerpt`) en el bloque `__main__`. Falta
  la función que descargue el documento del 8-K y extraiga el texto relevante
  (probablemente el Item 2.02 o el press release adjunto) — es un paso de
  scraping adicional, del mismo tipo que `edgar_scraper.py`, no un cambio de
  diseño.
- **El orquestador que llama a `compute_car` y `run_backtest_for_event` sobre
  TODOS los eventos de la BD** (el "día 4" del plan): están las funciones
  (`pipeline/backtest/backtester.py`) y están probadas con datos sintéticos,
  pero falta el script que las recorra sobre la tabla `events` real, arme el
  panel de precios/factores desde Postgres, y escriba en `backtest_runs`.
  Es la pieza que conecta lo ya construido, no lógica nueva.

Ninguna de estas ausencias es una limitación de diseño — son, literalmente,
las partes que necesitan datos reales (EDGAR, FDA, o eventos ya cargados en
la BD) que este sandbox no puede descargar para escribir contra ellas con
confianza. Escribirlas a ciegas habría sido peor que dejarlas explícitas aquí.
