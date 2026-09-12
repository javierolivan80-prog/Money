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
- Los **288 tests** de `pipeline/tests/` pasan (39 Fase 1 + 73 Fase 2 + 36
  Fase 3 + 81 backtest de cartera + 30 paper trading + 2 regresión Fase 5 +
  27 Fase 6/validación estadística), incluyendo decenas contra un
  **Postgres 16 real** levantado en este sandbox (no un mock) — schema,
  upserts idempotentes, CHECK constraints anti-look-ahead, detección de gaps
  de supervivencia, la caché de 24h de Bull/Bear/Judge, el filtro
  anti-look-ahead de análogos históricos y de filings previos
  (guidance/rumor), un test de integración de extremo a extremo de las 8
  etapas de análisis (enrichment → novelty → Bull/Bear/Judge → impact → EV →
  abstención → persistencia) con un cliente de Anthropic simulado, y (Fase 4)
  el backtest de cartera completo (3 versiones, TP/SL/trailing stop,
  max_concurrent, curva de equity, ~15 métricas) verificado con precios
  sintéticos diseñados para disparar cada mecanismo de salida al menos una
  vez, más la reconciliación exacta de caja.
- El motor de backtest pasó **T1 (placebo)** y una réplica sintética de
  **T2** con datos generados, no reales — ver `ARCHITECTURE_LEAN.md` §8 para
  qué significa cada uno.
- El dashboard de Next.js compila, y se probó en runtime contra ese mismo
  Postgres real: el estado vacío, el estado "sin runs todavía", y el
  renderizado completo con 30 trades sintéticos (equity curve SVG, tabla,
  métricas) — esto encontró y corrigió un bug real (node-postgres devuelve
  columnas NUMERIC como string, no number). La página nueva `/portfolio`
  (Fase 4) se probó igual, en el navegador (capturas con Playwright, no solo
  `next build`) contra 40 eventos sintéticos con las 3 versiones simuladas —
  esto encontró y corrigió un bug real distinto, del lado de Python: los
  `findings` de `generate_recommendation()` interpolaban el float crudo de
  Sharpe/drawdown/calibración sin formatear en la rama que NO supera los 3
  criterios (sí lo hacía en la rama que los supera), así que salían cadenas
  como `Sharpe=-4.1555154199129225` en vez de `Sharpe=-4.16` — invisible en
  los tests (que solo comparaban el string por `in`, no su formato exacto)
  hasta que se vio renderizado.
- **Lo que NO se validó, porque no hay red hacia esos hosts desde aquí:** el
  scraper de EDGAR, el backfill de yfinance, el fetcher de Ken French, y
  (Fase 3) el extractor de texto de filings (`filing_text.py`) nunca se han
  ejecutado contra los servidores reales. Siguen el formato público
  documentado, pero cada uno lleva una advertencia en su docstring señalando
  exactamente qué verificar a mano antes de confiar en un backfill completo.
- **Lo que tampoco se validó, porque tampoco hay clave**: ninguna llamada a
  la API de Anthropic (Bull/Bear/Judge) se ha hecho en vivo — ni en la Fase 1
  ni en la Fase 2. Se probó la construcción de las requests (modelos, JSON
  schemas exactos del spec) y el parseo de respuestas con la forma exacta que
  documenta el SDK, con mocks — nunca contra el servidor real.

### 0.1 Sobre la premisa de la Fase 2 ("tienes tabla events con 50k+ eventos")

Esa premisa no es cierta todavía: `events` está vacía en este entorno (nada
se ha ejecutado contra EDGAR real, ver arriba). La Fase 2 se construyó y
validó igual que la Fase 1 — con Postgres real y datos sintéticos donde no
hay datos reales que usar — para que el código esté listo el día que la Fase
1 corra de verdad en GitHub Actions (§3). El pipeline entero (Etapas 1-8) es
correcto y está probado; lo que falta es que existan eventos reales sobre
los que correrlo.

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

### 3.4 Extraer texto de filings (Fase 3 — insumo de Bull/Bear y novelty)

**Nunca se ha descargado el cuerpo de un filing real desde este sandbox**
(mismo problema de red que el resto — AUDIT_LEAN.md §1.5). El parser sigue
el formato "full submission text file" de EDGAR (bloques `<DOCUMENT>` con
TYPE/SEQUENCE/FILENAME/TEXT), pero antes del backfill completo:

```bash
export EDGAR_USER_AGENT="Tu Nombre tu@email.com"
python -m pipeline.ingest.filing_text --single-event <un event_id real>
```

Lee el texto extraído a mano: ¿quedó HTML/CSS suelto? ¿se cortó a mitad de
frase? Para un evento de earnings (8K_2.02), confirma que se usó el exhibit
de prensa (`includes_exhibit=True`) y no solo el cuerpo del 8-K — el 8-K en
sí casi siempre es solo "ver el comunicado adjunto", sin la noticia real.

Backfill completo:

```bash
python -m pipeline.ingest.filing_text
```

Los eventos de FDA (`source != 'EDGAR'`) se saltan automáticamente — no hay
scraper de FDA todavía (§6).

### 3.5 Backfill de precios (yfinance) — el paso más frágil
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

### 3.6 Factores Fama-French

```bash
python -m pipeline.ingest.fama_french
```

### 3.7 Calcular CAR de eventos (insumo de los análogos históricos)

```bash
python -m pipeline.backtest.populate_car_results
```

Sin esto, la Etapa 6 (`analyze/historical_analogues.py`) verá `n_analogues=0`
para todo, y el EV/abstención de la Fase 2 se calculará sin ningún análogo
histórico real detrás — no falla, pero pierde la mitad de su fundamento.
Correrlo después de tener precios (§3.5) y factores (§3.6) cargados.

### 3.8 Smoke test del pipeline de análisis (Fase 2, Etapas 1-8)
**Nunca se ha llamado a la API de Anthropic en vivo desde esta sesión** (sin
`ANTHROPIC_API_KEY` configurada aquí, ni en la Fase 1 ni en la Fase 2). Antes
del backfill completo de ~25k eventos, limita el orquestador a un puñado
editando temporalmente `CHUNK_SIZE` en `event_analysis_pipeline.py`, o
simplemente lánzalo sobre una tabla `events` pequeña primero:

```bash
export ANTHROPIC_API_KEY="tu-clave"
python -m pipeline.analyze.event_analysis_pipeline
```

Revisa a mano varias filas de `event_analyses`: ¿las tesis Bull/Bear tienen
sentido para el evento? ¿`net_conviction` del Judge parece razonable dado el
Bull/Bear que recibió? ¿`trade_decision_balanced` coincide con lo que
esperarías mirando `abstention_decision` a mano? Es más barato encontrar un
prompt mal calibrado en 5 eventos que en 25.000.

Después, revisa las estadísticas del día 3 (pedidas por el spec):

```bash
python -c "
from pipeline.db.connection import get_connection
from pipeline.analyze.event_analysis_pipeline import compute_day3_stats
import json
print(json.dumps(compute_day3_stats(get_connection()), indent=2))
"
```

Si `pct_trade_*` sale muy fuera de 10-70%, la función ya te da una
recomendación de qué umbral tocar en `ev_engine.EV_THRESHOLDS` — pero
**el ajuste es manual, a propósito** (ver docstring de `compute_day3_stats`:
auto-ajustar umbrales mirando la salida de la misma corrida que se evalúa es
el tipo de sobreajuste que `AUDIT_LEAN.md` prohíbe explícitamente).

### 3.9 Backtest de cartera (spec "Fase 3 — Backtesting", Etapas de simulación)

Nombrado "Fase 3" en el spec de backtesting que lo pidió, pero es la **Fase 4**
en la numeración de este repo (la Fase 3 ya la ocupa la extracción de texto de
filings, §3.4-§3.5) — se usa "Fase 4" en el resto de esta sección para no
confundir los dos.

Requiere que `event_analyses` tenga filas con `trade_decision_* != 'NO_TRADE'`
(Fase 2/3.8) y precios con `open_raw` (yfinance_backfill.py ya lo descarga —
ver §3.6 más arriba). Corre las 3 versiones (Conservative/Aggressive/Balanced)
y escribe en `portfolio_trades` + `portfolio_equity_curve`:

```bash
python -m pipeline.backtest.portfolio_report
```

Esto imprime la recomendación final y las métricas de cada versión. Para el
reporte completo en JSON (curvas de equity, submétricas por tipo de evento,
calibración, top 10 ganadores/perdedores, scatter predicho-vs-real):

```bash
python -c "
from pipeline.db.connection import get_connection
from pipeline.backtest.portfolio_report import run_full_backtest
import json
report = run_full_backtest(get_connection(), run_batch_tag='manual-check')
print(json.dumps(report, indent=2, default=str))
"
```

**Antes de confiar en cualquier número de aquí**, revisa
`report['versions'][version]['no_lookahead_violations']` — si esa lista no
está vacía, el resultado de esa versión no es de fiar (ver
`backtest/portfolio_validation.py`). El `run_full_backtest` no aborta si hay
violaciones (para que puedas ver TODAS las de una vez), pero
`generate_recommendation()` sí las trata como bloqueantes: cualquier versión
con violaciones nunca puede recibir un "SÍ" en el veredicto final.

`run_full_backtest()` guarda el reporte completo (JSONB) en
`portfolio_reports`, una fila por `run_batch_tag` — el dashboard de Next.js
(`app/`, ver §4) lo lee de ahí en `/portfolio` sin reimplementar ninguna
fórmula de `portfolio_metrics.py`. Para verlo localmente sin esperar al
pipeline nocturno: corre `portfolio_report.py` como arriba, luego
`cd app && npm run dev` y abre `http://localhost:3000/portfolio`.

**Lo que la página SÍ dibuja**: las ~15 métricas de riesgo/desempeño, la
curva de equity en dólares (SVG a mano, mismo patrón que `EquityCurve.tsx`
de la Fase 1), calibración, submétricas por tipo de evento (con el aviso de
n<20), estabilidad temporal, top 10 ganadores/perdedores, el reporte de
sesgos, y el veredicto final con sus findings. **Lo que NO dibuja**: el
scatter predicho-vs-real (`prediction_regression.scatter` sí viaja en el
JSON, listo para plotear — pintar el scatter en sí quedó fuera de esta
sesión por ser el único output puramente visual sin una tabla ya
equivalente).

**Ambigüedades del spec, resueltas y documentadas en el código, no aquí en
detalle** — ver las cabeceras de módulo para el razonamiento completo:
- `portfolio_strategies.py`: el take-profit de Conservative ("+2% o +3%"),
  el patrón de tramos del trailing stop de Aggressive ("+20%→30%, etc."), y
  cómo Balanced decide qué trade ejecuta con reglas de Conservative vs
  Aggressive.
- `portfolio_simulator.py`: qué gana cuando un stop-loss y un take-profit se
  cruzan el mismo día (gana el stop-loss, convención conservadora), y cómo
  se consolida una posición con cierres parciales por trailing stop en una
  sola fila de `portfolio_trades`.
- `portfolio_metrics.py`: la fórmula de calibración es literal del spec
  (`1 - |predicho-real|/|predicho|`) y puede salir fuera de [0,1] — no se
  recorta, se reporta tal cual.

### 3.10 Paper trading simulado (spec "Fase 4 — Paper Trading simulado")

Simula la ÚLTIMA SEMANA COMPLETA (lunes-viernes) de datos disponibles como si
fuera "futura": entrada D+1, hold hasta TP/SL o fin de semana, log con status
`OPEN`/`CLOSED_TP`/`CLOSED_SL`/`CLOSED_TIMEOUT` (tabla `paper_trades`), más
la comparación predicción-vs-real para TODOS los eventos de la semana (con o
sin trade_decision) y las alerts del spec. Requiere lo mismo que §3.9
(`event_analyses` con trade_decision, precios con `open_raw`):

```bash
python -m pipeline.paper_trading.report
```

Guarda el reporte completo en `paper_trading_reports` (JSONB), mismo patrón
que `portfolio_reports` — el dashboard lee de ahí, no recalcula nada.

**Estado OPEN de verdad, no un placeholder**: a diferencia del backtest
histórico (que fuerza el cierre de todo al final del panel de precios,
porque ahí termina "la historia"), aquí una posición sin TP/SL disparado y
sin datos de precio todavía hasta el fin de semana simulada se queda
`OPEN` — es la respuesta correcta ("todavía no lo sabemos"), no un caso sin
cubrir. Volver a correr el mismo comando más tarde (con más días de precio
ya cargados) resuelve la posición de forma natural, porque el
`run_batch_tag` se deriva de la SEMANA simulada (`paper-week-{lunes}`), no
de la fecha de hoy — así el pipeline nocturno actualiza las MISMAS filas
noche tras noche en vez de dejar huérfanas las posiciones `OPEN` de ayer
bajo un tag distinto (bug real que se detectó y corrigió en esta sesión
antes de conectar el paso a `nightly_pipeline.yml` — ver el historial de
`paper_trading/report.py`).

**Ambigüedades del spec, resueltas y documentadas en el código**:
- `paper_trading/simulator.py`: sin los tramos de trailing-stop de
  Aggressive (el log del spec es de una sola pieza) — se usa el primer
  umbral del trailing real (+20%) como take-profit único estand-in, solo
  para esta simulación de una semana (el backtest histórico de §3.9 sigue
  usando los tramos reales). Sin sizing en dólares (el spec no lo pide,
  solo `pnl_pct`) — se reutiliza la misma comisión de 10 bps del backtest
  histórico para que la comparación "¿el paper trading confirma el
  backtest?" sea consistente.
- `paper_trading/analysis.py`: el spec ilustra `predicted_magnitude` con
  una escala grande ("predice +40%, sube +38%") que no corresponde a
  `ev_conservative/aggressive/balanced` (0.2%-2%, un valor esperado
  ajustado por probabilidad, no un pronóstico de movimiento) sino más bien
  a `impact_estimation.expected_magnitude_pct` (Etapa 6) — que solo se
  persiste como texto formateado dentro de un JSONB, no como número
  consultable. Se usa `ev_{version}*100` como predicted_magnitude, la MISMA
  definición que ya usa `portfolio_metrics.compute_calibration` (Fase 3),
  para que "calibración" signifique lo mismo en todo el proyecto — y se
  recalibran los umbrales de los alerts a esta escala real, no a los
  "+40%" ilustrativos del spec.
- `calibration_score` en esta fase es una CORRELACIÓN(confidence, accuracy)
  — literal del spec de Fase 4 — DISTINTA de la fórmula de calibración de
  la Fase 3 (`1-|predicho-real|/|predicho|`, en `portfolio_metrics.compute_calibration`).
  Son dos métricas con el mismo nombre en dos specs distintos del usuario:
  se implementan ambas, cada una donde su spec la pidió, sin intentar
  unificarlas — ver `compute_calibration_diagnostics` (correlación + Brier
  + ECE, compartida entre esta fase y el dashboard) vs `compute_calibration`
  (la fórmula de razón de la Fase 3).

### 3.11 Dashboard — Fase 5 ("Dashboard con 3 columnas paralelas")

Rediseño completo de `app/` sobre el spec de la Fase 5. Sustituye al
dashboard de la Fase 1 (que leía `backtest_runs`, el event study de una
ventana fija de 20 días) — ese backend sigue existiendo y corriendo cada
noche (sigue siendo la fuente de `AUDIT_LEAN.md` §2.2.3 y de
`event_study.py`, Fase 6), pero ya no tiene página propia: sus números
"¿existe un edge?" son un dato de fondo, no lo que este dashboard enseña
(que responde "¿es operable?").

**Rutas** (barra de navegación compartida, `components/Nav.tsx`):
- `/` — Overview: header + 3 columnas paralelas (Conservative/Balanced/
  Aggressive), cada una con curva de equity combinada (histórico +
  overlay de paper trading de la semana, Recharts), métricas clave,
  posiciones abiertas y últimas 5 cerradas de paper trading, y el
  finding de la recomendación para esa versión.
- `/signals` — TAB 1 "All Signals": feed cronológico de TODOS los eventos
  analizados (con o sin trade_decision), con filtros (ticker, event type,
  signal, rango de fechas, confidence mínima) resueltos en SQL en el
  servidor — Tanstack Table solo ordena/pagina lo ya filtrado. Bull/Bear/
  Judge colapsable por fila.
- `/portfolio` — TAB 2 "Backtest Analysis": el detalle completo del
  backtest histórico (extendido en esta fase con scatter predicho-vs-real,
  win rate por banda de confidence, histograma de retornos, drawdown/
  underwater plot — los 4 en Recharts) + el botón de exportar PDF.
- `/calibration` — TAB 3: curva de calibración (confidence vs win rate
  real, con la diagonal de referencia), Brier score, ECE, e
  interpretación textual (over/under/bien calibrado) con una sugerencia
  de ajuste. **Sin sliders interactivos**: el spec pide "sliders: ajustar
  confidence futura" pero no hay ningún mecanismo que consuma ese ajuste
  (ni un endpoint, ni una re-simulación) — un slider que no cambia nada
  sería un elemento de UI que miente sobre lo que hace. Se muestra la
  sugerencia de ajuste como texto en su lugar.
- `/comparison` — TAB 4: tabla lado a lado de las 3 versiones (Tanstack
  Table).
- `/week` — TAB 5 "Signals This Week": vista en vivo del paper trading de
  la semana — trades ejecutados/abiertos/cerrados, win rate, P&L diario
  (barras), calibración preliminar, y la comparación "¿coincide con el
  backtest histórico?" de `paper_trading/report.py`.

**Recharts y Tanstack Table**: primer uso de ambos en el proyecto —
pedidos explícitamente por este spec (a diferencia del spec de la Fase 1,
que pedía deliberadamente evitar dependencias de charting; se sigue la
instrucción más reciente del usuario). `@tanstack/react-table` debe fijarse
en la línea 8.x — la versión "latest" que instala npm por defecto en este
momento es una v9 con una API completamente distinta (`createCoreRowModel`
en vez de `getCoreRowModel`, etc.), no la API estándar documentada que
espera el resto del ecosistema.

**Export PDF** (`components/ExportPdfButton.tsx`, botón en `/portfolio`):
genera un PDF real en el cliente con jsPDF + jspdf-autotable — resumen,
recomendación, tablas de métricas por versión, top 5 ganadores/perdedores,
event study por clase. **No incrusta las curvas de equity como imagen**:
capturar un SVG de Recharts a canvas (html2canvas) es frágil (fuentes,
tainted canvas, tamaño variable) para el beneficio que aporta en un POC;
en su lugar, cada curva se representa como su tabla de puntos clave
(balance inicial/final/pico/valle). Probado de extremo a extremo con
Playwright (`page.waitForEvent('download')`), no solo con `next build`.

**Bug real encontrado durante esta fase** (no en código nuevo — en
`portfolio_simulator.py`, ya en producción desde la Fase 3/4): si la
entrada D+1 de un evento resulta ser el ÚLTIMO día de precio disponible
para ese ticker (cero días posteriores para observar la posición),
`compute_target_date` degenera a `target_date == entry_date`, y el cierre
forzado al final del backtest generaba `exit_date == entry_date` — una
violación real de la constraint anti-look-ahead (`chk_portfolio_no_lookahead`
la habría rechazado en un INSERT real; en memoria, el assert de
`consolidate_trade_record` la atrapó primero). Nunca se había disparado
porque ningún dato sintético anterior tenía un evento tan pegado al final
del panel de precios — apareció al construir datos de demo para probar el
dashboard con paper trading en el borde del calendario. Corregido en
`_build_entry_plan`: un evento así se omite (mismo criterio que "sin
precios posteriores a D0"), con test de regresión en
`test_portfolio_simulator.py`.

**Componentes retirados**: `StrategyColumn.tsx` y `EquityCurve.tsx` (Fase
1) — sin uso una vez que `/` pasó a leer `portfolio_reports` +
`paper_trading_reports` en vez de `backtest_runs`. Las queries
correspondientes (`getStrategySummaries`, `getEquityCurve`, `getTrades`,
`getLatestRunBatchTag`) también se eliminaron de `lib/queries.ts`.

### 3.12 Validación estadística final — Fase 6 ("Validación + Recomendación de inversión")

Ensambla TODO lo anterior (event study, backtest, calibración,
sensibilidad, sesgos) en un único documento de decisión — el último paso
del spec del usuario, "¿invertir dinero real?". Nuevo paquete
`pipeline/validation/`:

- `event_study.py` (PARTE 1): estadísticos por `event_class` sobre
  `car_results` (no sobre los trades del backtest — es la pregunta "¿existe
  un edge?", con n en la escala de todos los eventos, ver
  `AUDIT_LEAN.md` §2.2.3). MDE = 2.8·σ/√n, la MISMA fórmula y constante que
  ya usó el propio audit — no se reinventa. t-test de una muestra (scipy)
  contra H0: retorno medio = 0.
- `sensitivity.py` (PARTE 5): 5 escenarios — comisión +0.1%, spread +0.2%,
  latencia D+2 (aproximado: reprecia la entrada contra el MISMO exit ya
  registrado, no resimula el día a día completo — ver el docstring del
  módulo para por qué esto basta para la pregunta que responde esta
  sección), confidence -20% (reutiliza el piso de confidence real de
  `abstention_engine.py`, no un umbral inventado), y régimen alto/bajo VIX
  en la entrada (proxy de la varianza YA OBSERVADA en vez de una
  resimulación estocástica bajo un choque hipotético de +50% — este
  proyecto no tiene un modelo de precios). La misma tabla alto/bajo VIX
  responde a la vez la pregunta de "regime dependency" de PARTE 4.
- `decision.py` (PARTE 6): las 3 opciones literales del spec
  (GREENLIGHT/YELLOWLIGHT/REDLIGHT) con los umbrales EXACTOS que da —
  distintos de los umbrales de `portfolio_report.py:generate_recommendation`
  (Fase 3), que responde una pregunta más laxa ("¿esta corrida en concreto
  se ve bien?") con otros números. Una violación anti-look-ahead es
  bloqueante por sí sola, sin importar qué tan bien salga el resto.
- `report.py`: ensambla las 7 partes en Markdown y escribe
  `docs/VALIDATION_REPORT.md` + un CSV con todos los trades de la corrida
  (`docs/trades_{run_batch_tag}.csv`) — solo lectura de lo que ya calculan
  los módulos anteriores, ninguna fórmula nueva aquí.

Correrlo:

```bash
python -m pipeline.validation.report
```

**El `docs/VALIDATION_REPORT.md` que ya está commiteado en este repo es un
EJEMPLO real, generado en esta sesión contra ~11 meses de datos sintéticos**
(el mismo dataset de demo usado para probar el dashboard, con factores
Fama-French y VIX sintéticos añadidos para que el event study y el split
por régimen tuvieran algo que medir) — el propio documento lleva una
advertencia en su primera sección explicando esto, para que nadie lo
confunda con una recomendación de inversión real. Regenerarlo con datos
reales (tras completar §1-§3.11) sobrescribe el mismo archivo.

**No se añadió como paso de `nightly_pipeline.yml`**, a diferencia del
resto del pipeline: un runner de GitHub Actions es efímero, y este paso
escribe un ARCHIVO (no filas en Postgres) — sin un paso adicional de commit
automático (que este proyecto nunca ha hecho, y no es de fiar añadir sin
que lo pida el usuario), el archivo se perdería al terminar el job. Se deja
como un paso manual/local, igual que §3.2 (verificar EDGAR con un solo día
antes del backfill completo).

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
| 2 | Backfill completo de EDGAR, extracción de texto de filings (`filing_text.py`), ingesta FDA/RSS (no incluida — ver §6) | |
| 3 | Factores FF3, verificar `universe` con deslistados | |
| 4 | `populate_car_results.py` sobre eventos reales, luego `event_analysis_pipeline.py` (Fase 2) | |
| 5 | **T1 y T2 con datos reales** (aquí solo se validaron con datos sintéticos) | Bloqueante — no seguir sin esto |
| 6 | Pre-registro commiteado → `portfolio_report.py` (backtest de cartera) → `paper_trading/report.py` (semana en vivo) → una sola pasada OOS | |
| 7 | Dashboard (`app/`, §3.11) y `VALIDATION_REPORT.md` (§3.12) — ambos ya construidos y probados con datos sintéticos, listos para leer datos reales — decisión final | |

## 6. Lo que este commit NO incluye (y por qué)

- **Ingesta de FDA (openFDA + RSS)**: el spec de la Fase 1 pidió "EDGAR es el
  eje: earnings + 8-K. FDA es satélite exploratorio" — se priorizó construir
  y validar bien la pata EDGAR (que es donde vive la potencia estadística,
  `AUDIT_LEAN.md` §2.2.3) antes que dispersar el esfuerzo. Añadir
  `pipeline/ingest/fda_scraper.py` siguiendo el mismo patrón de
  `edgar_scraper.py` es directo cuando llegue el momento. La Fase 2 ya tiene
  la lógica que depende de eventos FDA lista para cuando existan
  (`abstention_engine.py` regla 6, `check_fda_crl_without_8k` en el
  orquestador) — solo falta que la tabla `events` tenga filas con
  `source IN ('FDA_RSS','FDA_OPENFDA')`.
- **Extracción del texto real del filing**: esto YA NO es un hueco —
  `pipeline/ingest/filing_text.py` (Fase 3) lo cierra: descarga el submission
  completo, separa los documentos `<DOCUMENT>`, y prefiere el exhibit de
  prensa (`EX-99*`) sobre el cuerpo del 8-K para earnings (que suele ser solo
  "ver el comunicado adjunto"). `adversarial_analyzer.py` ya recibe el texto
  real cuando existe (con fallback al placeholder si aún no se ha extraído),
  y `pipeline/analyze/guidance_detector.py` calcula `has_prior_guidance`/
  `rumor_flag` para `novelty.py` a partir de filings previos del mismo
  ticker — por palabras clave, no NLP semántico (limitación documentada en
  su propio módulo, no oculta). Ver §3.4.
- **CAR de eventos** (el "día 4" del plan de `ARCHITECTURE_LEAN.md`): esto
  YA NO es un hueco — `pipeline/backtest/populate_car_results.py` se
  construyó en la Fase 2 precisamente porque `historical_analogues.py`
  dependía de él. Ver §3.7.
- **Ingesta de FDA sigue siendo el único hueco real que queda** de los tres
  originales — y con ella, la ingesta del texto de filings de FDA (distinto
  formato al de EDGAR, `filing_text.py` no lo cubre) y la detección de
  guidance/rumor sobre ese texto tampoco existen todavía. Todo lo demás de
  la Fase 3 (Bull/Bear con texto real, novelty con guidance/rumor) funciona
  igual de bien para EDGAR sin esperar a esto.
- **El dashboard de Next.js**: esto YA NO es un hueco — `app/` es ahora el
  dashboard completo de la Fase 5 (§3.11): overview de 3 columnas con
  overlay de paper trading, y los 5 tabs del spec (All Signals, Backtest
  Analysis, Calibration, Comparison, Signals This Week), con Recharts y
  Tanstack Table, más export a PDF. Lo único que se dejó fuera a propósito:
  los sliders de "ajustar confidence" de TAB 3 (no hay ningún mecanismo que
  consuma ese ajuste — ver §3.11) y la actualización en tiempo real vía
  WebSocket (el dashboard es `force-dynamic`: siempre lee datos frescos de
  Postgres en cada visita/recarga, que es lo que "cada cierre o refresco
  manual" pide el spec — un push en vivo no aporta nada sobre un batch
  nocturno).

Ninguna de estas ausencias es una limitación de diseño — son, literalmente,
las partes que necesitan datos reales (EDGAR, FDA, o el texto de filings ya
descargados) que este sandbox no puede obtener para escribir contra ellas
con confianza, o piezas de UI (sliders sin backend, WebSockets) que
quedaron fuera del alcance por no aportar nada real en un POC. Escribirlas
a ciegas habría sido peor que dejarlas explícitas aquí.
