# ARCHITECTURE_LEAN.md — Arquitectura mínima viable, solo fuentes gratuitas

Fecha: 2026-09-12
Alcance: TAREA 3
Prerrequisito: leer `AUDIT_LEAN.md` §2.2.3 (potencia estadística) y §2.5 (por qué todo es batch nocturno). Las decisiones de aquí salen de allí.

---

## 1. Qué es y qué no es este sistema

**Es** un motor de event study con backtest, que corre una vez al día, sobre eventos de EDGAR y FDA, con precios diarios.

**No es** un sistema de trading. No ejecuta. No opera en tiempo real. No tiene UI.

**La pregunta que tiene que responder, y la única:**

> ¿Existe un CAR anormal medio, estadísticamente significativo tras corrección por contrastes múltiples, en la ventana D+1→D+20, para al menos una clase de evento pre-registrada, que sobrevive a 25 bps de slippage y a validación fuera de muestra?

Todo lo que no sirva para responder eso, fuera. El resto de este documento es la aplicación de ese criterio.

---

## 2. Stack

| Capa | Elección | Por qué |
|---|---|---|
| Lenguaje | Python 3.11 | Único |
| Almacén de eventos | **SQLite** (un fichero) | Cero operación, transaccional, millones de filas sin despeinarse, versionable, copiable. Postgres solo si hubiera concurrencia, y no la hay |
| Panel de precios | **Parquet** (pyarrow) | El event study lee columnas de series largas; SQLite es mal formato para eso |
| Orquestación | `cron` + `make` | Sin Airflow, sin Prefect, sin Dagster |
| Contenedores | **Ninguno** | Un venv |
| Librerías | `requests`, `pandas`, `numpy`, `statsmodels`, `scipy`, `pyarrow`, `yfinance`, `python-dateutil` | Ocho. `statsmodels` es la que hace la regresión de factores |
| LLM | Claude API (`claude-haiku-4-5`) vía Batch API | Solo clasificación. Ver §6 |

Reglas duras: **sin framework web, sin Docker, sin cola de mensajes, sin ORM, sin servicio siempre encendido.** El sistema es un script que corre por la noche y deja ficheros.

---

## 3. Pipeline: cinco etapas, un cron

```
cron 02:00 ET
  │
  ├─ 1. ingest ────── EDGAR daily-index + openFDA + RSS  →  raw_filings, raw_fda
  │
  ├─ 2. classify ──── reglas (95%) + Claude batch (5%)   →  events
  │
  ├─ 3. prices ────── yfinance incremental               →  prices.parquet
  │
  ├─ 4. eventstudy ── CAR vs Fama-French 3               →  car_results
  │
  └─ 5. report ────── distribuciones + métricas          →  report.html
```

Cada etapa es idempotente y reejecutable por fecha. Si falla la 3, la 1 y la 2 no se repiten.

### Etapa 1 — `ingest`
- **EDGAR**: fichero `daily-index` del día (no full-text search — el índice diario es más simple y completo). Respetar el límite de 10 req/s y mandar `User-Agent` identificable, que la SEC lo exige.
- **openFDA**: endpoints `drugsfda`, `approved_CRLs`. Con API key gratuita: 240 req/min y 120.000/día, frente a 1.000/día sin clave. **Pide la clave el día 1**, es gratis y multiplica el límite por 120.
- **RSS**: FDA press releases, EDGAR latest filings. Gratis, ilimitado, mismo día.
- **NewsAPI**: *no se usa como fuente de señal.* 24 h de retardo y 100 peticiones/día. Solo metadato opcional. Si molesta, se cae entero del diseño y no se pierde nada.

### Etapa 2 — `classify`
Reglas primero, LLM después. El código de Item del 8-K ya clasifica el ~95% de los eventos sin tocar un modelo. Claude solo interviene en:
- Item 8.01 ("otros eventos"), que es heterogéneo por definición.
- Desambiguar titulares de FDA/RSS y ligarlos al emisor correcto.

Caché por hash de contenido: un documento ya clasificado nunca se reprocesa. Reejecutar el pipeline entero cuesta $0.

### Etapa 3 — `prices`
yfinance a un panel Parquet, incremental. Es la etapa lenta y frágil (§7).

### Etapa 4 — `eventstudy`
- Ventana de estimación `[-250, -30]`, modelo **Fama-French 3 factores** con datos de la Ken French Data Library (gratis).
- Ventanas de evento: `[0,+1]` (no operable, se mide igual), `[+1,+5]`, `[+1,+20]` (las operables).
- Volumen anormal, Amihud, volatilidad realizada.

### Etapa 5 — `report`
Un HTML estático. Distribuciones por clase, CAR medio con IC bootstrap, resultados de los tests del §8.

---

## 4. Modelo de datos

Seis tablas. No más.

```
universe      (cik, ticker, sic, first_seen, last_seen, delisted_date)
raw_filings   (accession, cik, form_type, item_codes, filed_at, url, fetched_at)
raw_fda       (source, application_no, sponsor, action_type, action_date, fetched_at)
events        (event_id, cik, ticker, event_class, event_date, D0_close_date,
               confidence, classifier, source_ref)
prices        → Parquet: (date, ticker, close_raw, adj_factor, volume, captured_at)
car_results   (event_id, window, car, abnormal_volume, t_stat, model)
```

Dos decisiones que importan más de lo que parecen:

**`close_raw` + `adj_factor` separados, con `captured_at`.** yfinance recalcula los cierres ajustados retroactivamente. Guardar solo el ajustado significa que tu backtest de hoy no es reproducible mañana. Esto es una fuente silenciosa de look-ahead.

**`D0_close_date` explícito.** Es la fecha en que el evento se considera público (el cierre de D). La entrada es siempre posterior. Al ser una columna y no una convención mental, el look-ahead se convierte en algo comprobable con un `assert`.

---

## 5. Qué se corta, y por qué no cuesta rigor

| Se corta | Por qué no duele |
|---|---|
| **Tiempo real** | §2.5 de la auditoría: la ventana operable es D+1→D+20. La latencia no vale nada ahí |
| **Todo lo intradía** | La señal es a días. Y yfinance solo da 1m de 30 días |
| **Datos de opciones** | No hay gratis. Limitación declarada, no chapuza oculta |
| **NewsAPI como señal** | 24 h de retardo. Sustituido por RSS, que es mejor y gratis |
| **Sentimiento de noticias** | Requiere datos de noticias que no tienes. No lo simules con un LLM sobre titulares de RSS: eso es ruido con aspecto de señal |
| **Micro-caps (<$300M, <$5)** | Dominan el conteo de eventos y nada de eso es operable. Su CAR es ruido y spread |
| **El universo completo de 8-K** | 5-6 clases pre-registradas. Barrer los 60+ Items es data mining |
| **Modelado de ejecución / VWAP** | Apertura y cierre, con un recargo fijo de slippage y un barrido de sensibilidad |
| **Condicionamiento multivariante en §5** | Con n=350 y 4 variables las celdas tienen n=5. Una variable, elegida a priori |
| **UI, API, dashboard, Docker, Postgres** | Nadie los va a usar en el POC |

### Lo que NO se puede cortar

| No se corta | Por qué |
|---|---|
| Ventana de estimación `[-250, -30]` | Sin ella no hay modelo de factores, y sin modelo de factores tu "alpha" es beta |
| Factores Fama-French (Ken French, gratis) | Mismo motivo. Es lo que separa el CAR del retorno crudo |
| Contabilidad de deslistados | Es la amenaza #1 a la validez (`AUDIT_LEAN.md` §2.4) |
| Holdout fuera de muestra | Sin esto el resultado no significa nada |
| Registro de contrastes múltiples | Sin esto encuentras "señal" garantizada |
| Los tests del §8 | Son lo que distingue esto de un backtest de foro |

---

## 6. Coste de Claude API

Tarifas verificadas: Haiku 4.5 $1 / $5 por MTok (entrada/salida); Sonnet 5 $2 / $10; Opus 5 $5 / $25. Batch API: **−50%**.

Carga de trabajo de backfill: ~25.000 eventos que necesitan LLM, a ~2.500 tokens de entrada y ~250 de salida.

| Modelo | Directo | Con Batch API |
|---|---|---|
| Haiku 4.5 | $94 | **$47** |
| Sonnet 5 | $188 | $94 |

**Recomendación: Haiku 4.5 + Batch API para el backfill.** Es clasificación a taxonomía cerrada, no razonamiento — no necesita un modelo grande, y el backfill no es sensible a latencia. Con caché de prompt sobre la taxonomía compartida baja más.

Incremental nocturno: ~30 eventos/noche ≈ **céntimos al día**.

**Coste total del POC: $50-100 una vez, más calderilla. Datos: $0.**

---

## 7. El riesgo real del plan: yfinance

Se dice pronto: "descargar 5 años de precios de 2.500 tickers". En la práctica:

- yfinance no es una API oficial, hace scraping de endpoints de Yahoo. El rediseño de Yahoo de febrero de 2025 rompió scripts en masa.
- Se reportan errores 429 alrededor de los ~950 tickers seguidos.
- El rate limit no está documentado y cambia sin aviso.

**Mitigación, y es lo que decide si la semana se cumple:**
1. **Lanza el backfill el día 1, en segundo plano, y que corra mientras construyes lo demás.** Es la dependencia crítica. Si lo dejas para el día 3, pierdes la semana.
2. Descarga por lotes con pausa entre ellos y reintentos con backoff exponencial.
3. `requests-cache` en disco: nunca pidas dos veces lo mismo.
4. Reanudable: si peta en el ticker 1.400, que siga desde el 1.400.
5. Congela el panel en Parquet y **no lo vuelvas a bajar**. El backfill se hace una vez.

---

## 8. Tests mínimos que demuestran que el sistema es serio

No son tests unitarios. Son **tests de falsación**: cada uno puede tumbar el resultado, y ese es el objetivo. Un sistema que no puede fallar ninguno no ha demostrado nada.

### T1 — Placebo con fechas aleatorias `(imprescindible)`
Corre el pipeline idéntico sobre fechas aleatorias, emparejadas por ticker y distribución de calendario.
**Debe dar CAR ≈ 0 y cero edge.** Si el placebo muestra edge, tu modelo de factores o tu cálculo de retornos está roto, y todo lo demás es artefacto. Es el test más barato y el que más veces salva un proyecto.

### T2 — Réplica de un efecto conocido `(imprescindible)`
Reproduce con tu propio pipeline un resultado documentado en la literatura: el CAR negativo tras anuncios de restatement (Item 4.02), del orden de −5% a −10%.
**Si tu pipeline no reproduce un resultado de manual, está roto.** No sigas hasta que lo haga. Este es el mejor test del conjunto porque valida el motor entero contra una verdad externa.

### T3 — Auditoría de look-ahead
- `assert` de que ninguna característica usa datos posteriores a `D0_close_date`.
- Desplaza todas las fechas de evento +1 día: el resultado debe degradarse en la dirección esperada. Si no cambia, no estabas midiendo el evento.

### T4 — Auditoría de supervivencia
Publica: % de eventos sin datos de precio, y la composición por clase de los que faltan frente a los que están. Si una clase pierde >20%, su resultado se marca como no fiable. (`AUDIT_LEAN.md` §2.4)

### T5 — Contrastes múltiples
Pre-registra las hipótesis en `docs/PREREGISTRATION.md` **commiteado antes de correr el backtest**. Reporta cuántos contrastes se hicieron y aplica Benjamini-Hochberg. El git log es la prueba de que no hubo p-hacking, y es gratis.

### T6 — Fuera de muestra, una sola vez
Ajusta y elige sobre **2021-2023**. Congela. Evalúa sobre **2024-2025 una única vez**.
El número OOS **es** el resultado. Si lo miras y reajustas, ya no es fuera de muestra y has quemado el holdout.

### T7 — Sensibilidad a costes
Barrido de slippage: 0 / 10 / 25 / 50 bps. **Si el edge muere a 25 bps, no existe para un sistema retail.** Reporta la curva completa, no el mejor punto.

### T8 — Intervalos de confianza, no puntos
IC bootstrap sobre el CAR medio, más un test de signos no paramétrico. Nada de medias sin su intervalo. Con los n de la auditoría, los intervalos van a ser anchos — esa anchura es el resultado honesto.

### T9 — Reproducibilidad
Misma semilla + mismos insumos = salida idéntica byte a byte. Snapshots de datos pinneados y con fecha.

### T10 — Integridad de datos
Sin NaN en las ventanas; cada evento mapea a un CIK válido; sin eventos duplicados por accession; sin días de cotización que falten en mitad de una ventana.

**Umbral de aceptación:** T1 y T2 son bloqueantes. Si el placebo muestra edge o el efecto conocido no se replica, el sistema no está midiendo lo que dice medir y ningún resultado posterior vale.

---

## 9. Plan de 7 días

| Día | Entregable | Nota |
|---|---|---|
| **1** | Esqueleto, esquema SQLite, ingesta EDGAR. **Lanzar el backfill de yfinance en segundo plano.** Pedir la API key de openFDA. Verificar los conteos de 8-K con los `form.idx` | El backfill arranca hoy o la semana no se cumple |
| **2** | Ingesta openFDA + RSS. Reglas de clasificación por Item. Fallback a Claude en batch | |
| **3** | Ingerir los factores de Ken French. Consolidar el panel de precios. Construir `universe` desde EDGAR, con deslistados | El backfill debería estar acabando |
| **4** | Motor de event study: CAR vs FF3, volumen anormal, ventanas | El corazón del sistema |
| **5** | **T1 (placebo) y T2 (réplica).** Día de rigor. No se avanza hasta que ambos pasen | Bloqueante |
| **6** | Pre-registro commiteado → backtest in-sample → una sola pasada OOS. T3-T8 | El orden importa: pre-registro *antes* |
| **7** | `report.html`, T9-T10, redacción de conclusiones | |

**Ruta crítica:** día 1 (backfill) → día 4 (motor) → día 5 (tests). Los días 2 y 3 tienen holgura; el 1 y el 5 no.

**Si algo se cae, se cae en este orden:** primero la pata FDA (es un satélite, ver auditoría), luego el Item 8.01 con LLM, luego los horizontes extra. **El día 5 no se toca.** Un sistema con menos clases de evento y tests completos vale; uno con todas las clases y sin tests no vale nada.

---

## 10. Alcance del POC

**Entra:**
- 3-4 clases de evento de EDGAR con n suficiente (`AUDIT_LEAN.md` §2.2.3)
- 1 clase de FDA, **declarada como exploratoria y sin potencia estadística**
- Universo: US common equity, precio >$5, cap >$300M, ADV >$1M
- Periodo: 2021-2025
- Horizontes: D+1→D+5 y D+1→D+20
- Paper trading, sin ejecución

**No entra:** tiempo real, opciones, intradía, ejecución, UI, multi-activo, no-US.

---

## 11. Criterios de decisión al acabar la semana

| Resultado | Qué significa | Siguiente paso |
|---|---|---|
| T1 o T2 fallan | El sistema no mide lo que dice | Arreglar antes de interpretar nada |
| CAR OOS significativo, sobrevive a 25 bps | Hay algo | Fase 1 de `DATA_REQUIREMENTS_PHASED.md` |
| CAR significativo, muere a 25 bps | Edge real pero no capturable retail | No gastar en datos. Replantear horizonte o universo |
| CAR no significativo, IC ancho | **No concluyente** — el caso más probable en la pata FDA | Mirar el MDE: ¿faltó n o faltó edge? Son problemas distintos |
| CAR no significativo, IC estrecho | No hay edge en esa clase | Descartar la clase. Resultado válido y útil |

**Sobre el cuarto caso, que es el que más probablemente te toque:** "no concluyente" no es fracaso, y tampoco es permiso para seguir gastando. Es la señal de que el diseño no tenía potencia para responder la pregunta. La respuesta correcta entonces es cambiar la clase de evento (más n), no comprar datos.
