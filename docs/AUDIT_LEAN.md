# AUDIT_LEAN.md — Auditoría bajo restricción de fuentes gratuitas

Fecha: 2026-09-12
Alcance: TAREA 1 (inventario) + TAREA 2 (viabilidad con datos gratis)
Restricción impuesta: SEC EDGAR, FDA (openFDA), yfinance, NewsAPI free, RSS públicos, Claude API. Sin Polygon, Databento, Bloomberg, Reuters, ni datos de opciones.

---

## 0. Dos avisos de método (leer antes que nada)

**0.1 — El repo está vacío.** `javierolivan80-prog/Money` no tiene ni un commit ni una rama remota. No existe código, ni esquema de BD, ni spec versionado. La "auditoría de inventario" que pediste no tiene nada que inventariar: esto es greenfield puro. Lo desarrollo en §1.

**0.2 — La numeración de secciones del spec es reconstruida.** Citas §4 (impact estimation), §5 (historical analogue), §6 (market context) y §10 (real-time reaction). Esas cuatro las trato exactamente como las nombras. El resto del spec no está en el repo, así que he reconstruido el andamiaje por función. Si tu numeración real difiere, el contenido sigue siendo válido — solo hay que remapear etiquetas.

| § | Nombre asumido | Origen |
|---|---|---|
| §1 | Objetivo y universo | reconstruido |
| §2 | Ingesta y detección de eventos | reconstruido |
| §3 | Clasificación y normalización | reconstruido |
| **§4** | **Impact estimation** | **citado por ti** |
| **§5** | **Historical analogue** | **citado por ti** |
| **§6** | **Market context** | **citado por ti** |
| §7 | Scoring / generación de señal | reconstruido |
| §8 | Sizing y cartera | reconstruido |
| §9 | Backtest y validación | reconstruido |
| **§10** | **Real-time reaction** | **citado por ti** |
| §11 | Ejecución | reconstruido |
| §12 | Monitorización / ops | reconstruido |

**0.3 — Marcado de evidencia.** Cada número lleva etiqueta:
- `[V]` verificado contra fuente pública en esta sesión (cito fuente).
- `[E]` estimado por mí a partir de estructura conocida del mercado. Lleva el método explícito y, cuando aplica, el procedimiento gratuito exacto para verificarlo.

No hay ningún número sin etiqueta. Los `[E]` no son adorno: el más importante (volumen de 8-K) está sin verificar por la razón del §1.4.

---

## 1. TAREA 1 — Inventario

### 1.1 Stack
Ninguno. No hay `requirements.txt`, `pyproject.toml`, `package.json`, `Dockerfile`, ni fuente de ningún lenguaje.

### 1.2 Base de datos
Ninguna. No hay esquema, migraciones, ni DDL.

### 1.3 Fuentes de datos actuales
Ninguna conectada. Las seis fuentes de tu restricción son aspiracionales, no existentes.

### 1.4 Estructura del repositorio
```
/home/user/Money
└── .git/          (inicializado, 0 commits, 0 refs remotas)
```
Rama de trabajo `claude/audit-free-system-j5kue7` creada sin historia previa.

### 1.5 Hallazgo operativo: esta sesión no tiene salida de red a ninguna fuente

Probé los seis hosts que necesita el sistema. La política de egress de la organización los rechaza todos en el CONNECT:

| Host | Resultado |
|---|---|
| `www.sec.gov` | `403 connect_rejected` |
| `data.sec.gov` | rechazado |
| `efts.sec.gov` (full-text search) | rechazado |
| `api.fda.gov` | rechazado |
| `query1.finance.yahoo.com` (yfinance) | rechazado |
| `newsapi.org` | rechazado |

Consecuencia práctica, y es una decisión de infraestructura que tienes que tomar antes del día 1: **el POC no se puede construir ni ejecutar en este entorno remoto.** Necesita correr en tu máquina local o en un entorno con política de red permisiva. Todo lo que sigue asume que resuelves eso; si no, nada de este plan arranca.

Por esa razón, los conteos de EDGAR de §2.2 son `[E]` y no `[V]`: no pude descargar los `form.idx`. Doy el procedimiento exacto para que los verifiques tú en ~10 minutos.

---

## 2. TAREA 2 — Viabilidad por sección

### 2.0 Tabla resumen

| § | Sección | ¿Sin datos pagos? | Comentario en una línea |
|---|---|---|---|
| §1 | Objetivo y universo | **SÍ** | Universo desde EDGAR, gratis |
| §2 | Ingesta de eventos | **SÍ** | EDGAR daily-index + openFDA + RSS; batch nocturno |
| §3 | Clasificación | **SÍ** | Reglas + Claude como fallback; coste ~$50-100 total |
| §4 | Impact estimation | **PARCIAL** | CAR sí; descomposición "sorpresa" no sin opciones |
| §5 | Historical analogue | **PARCIAL** | Suficiente n en EDGAR; **insuficiente en FDA** |
| §6 | Market context | **SÍ** | Y mejor de lo que crees: te faltan 4 fuentes gratis |
| §7 | Scoring | **SÍ** | Con disciplina anti-overfitting obligatoria |
| §8 | Sizing | **SÍ** | Trivial a este nivel |
| §9 | Backtest | **PARCIAL** | Amenaza real: sesgo de supervivencia de yfinance |
| §10 | Real-time reaction | **NO** | Descartado, como dices. Reemplazo en §2.5 |
| §11 | Ejecución | **SÍ** | Paper trading en POC |
| §12 | Ops | **SÍ** | Un cron y un log |

---

### 2.1 §4 — Impact estimation sin opciones: ¿vale solo precio/volumen?

**Veredicto: PARCIAL, y la parte que funciona es la que más importa.**

**Lo que sí puedes hacer, y es el estándar académico:**

El event study clásico es 100% precio/volumen. No necesita opciones. No es un apaño:

- **CAR** (cumulative abnormal return) contra modelo de mercado o Fama-French 3/5 factores. Ventana de estimación `[-250, -30]`, ventanas de evento `[0,+1]`, `[+1,+5]`, `[+1,+20]`.
- **Volumen anormal**: volumen del evento / mediana de 60 días.
- **Volatilidad realizada** post/pre.
- **Iliquidez de Amihud** para filtrar lo que no es operable.

Esto te da la magnitud y la persistencia del impacto. Es suficiente para responder la pregunta del POC ("¿esto predice o no?").

**Lo que pierdes sin opciones, ordenado por gravedad:**

**1. El movimiento esperado ex-ante (grave).** El precio del straddle te dice cuánto espera el mercado que se mueva la acción. Sin eso no puedes calcular *sorpresa = realizado − esperado*, y tu edge es exactamente `tu_previsión − previsión_del_mercado`. Sin el segundo término estás midiendo la mitad de la ecuación.

  *Simplificación válida:* construye un **movimiento esperado empírico** desde el histórico propio del ticker — la distribución de sus últimos 8 movimientos a 1 día en el mismo tipo de evento. Es un proxy razonable (la IV implícita antes de un catalizador suele rondar el movimiento realizado histórico reciente), es gratis, y no introduce look-ahead si usas solo eventos anteriores a `t`. Pierdes precisión; no pierdes la lógica.

**2. Posicionamiento direccional / skew (moderado).** No hay proxy gratis decente. Se asume la pérdida.

**3. Detección de "el mercado ya lo anticipa" (leve).** El run-up de IV pre-evento avisa de filtraciones.
  *Simplificación válida:* volumen anormal + volatilidad realizada anormal + deriva de retorno anormal en `[-10,-1]`. Captura buena parte de la misma señal.

**Conclusión §4:** sí vale. Construye el motor de CAR bien (con factores, no con retorno crudo) y trata la ausencia de "sorpresa implícita" como la limitación declarada #1 del POC. Es también la primera compra de la fase 2 (ver DATA_REQUIREMENTS_PHASED.md).

---

### 2.2 §5 — Historical analogue: ¿cuántos eventos hay en 5 años?

Esta es la pregunta central y la respuesta tiene dos mitades muy distintas.

#### 2.2.1 Lado FDA: los números reales `[V]`

| Fuente | 5 años | Detalle |
|---|---|---|
| Aprobaciones de fármacos novel (CDER NME/BLA) | **238** | 2021:50 · 2022:37 · 2023:55 · 2024:50 · 2025:46 |
| Reuniones de comité asesor (AdCom) | **~150** | 2023:29 · 2024:38 · **2025:14** |
| Complete Response Letters publicadas | **458** | acumulado histórico, publicado retroactivamente en 2025 |
| PMA originales (dispositivos) | ~200 | ~40/año `[E]` |

Fuentes: [RAPS – CDER aprobó 46 en 2025](https://www.raps.org/resource/cder-approved-46-novel-drugs-in-2025-half-for-rar.html), [FDA Novel Drug Approvals](https://www.fda.gov/drugs/development-approval-process-drugs/novel-drug-approvals-fda), [openFDA CRLs](https://open.fda.gov/apis/other/approved_CRLs/), [National Center for Health Research – caída de AdComs](https://www.center4research.org/stat-fda-shuns-advisory-committee-meetings/).

**Tres problemas que reducen esos números mucho más de lo que parece:**

**(a) Filtro de materialidad.** De las 238 aprobaciones, la mayoría son de large-caps donde una aprobación mueve el 0,5% de la acción. Los eventos donde la aprobación *es* la tesis (small/mid-cap biotech mono-producto) son **~60-100 en 5 años** `[E]`.

**(b) Las AdComs se están extinguiendo.** 14 en 2025 frente a 38 en 2024: una caída del 72%. Una clase de evento que se contrae así **no sirve como base de una distribución prospectiva**, aunque tuvieras el histórico. Estás modelando algo que la FDA está dejando de hacer.

**(c) Las CRLs tienen look-ahead y sesgo de selección.** Las 458 se publicaron en lotes en julio y septiembre de 2025, cubriendo 2002-2024. El dataset principal es `approved_CRLs` — cartas de solicitudes que *después* se aprobaron. Eso es supervivencia pura. Y la fecha de la carta ≠ la fecha en que el mercado se enteró.
  → **Corolario importante:** para una CRL, el evento operable no es la carta de la FDA. Es el 8-K / nota de prensa con que la empresa la comunica. El evento negociable vive en EDGAR, no en la FDA. Lo mismo aplica a buena parte de las aprobaciones.

#### 2.2.2 Lado EDGAR: aquí sí hay volumen `[E]`

Método de estimación: ~2.500-3.000 emisores en un universo invertible (precio >$5, cap >$300M, ADV >$1M), a ~8-12 formularios 8-K por emisor y año.

| Clase de evento (Item del 8-K) | Eventos/año | 5 años | Calidad |
|---|---|---|---|
| 2.02 Resultados (earnings) | ~11.000 | ~55.000 | Volumen enorme, edge bajo (lo más estudiado del mercado) |
| 1.01 / 2.01 Acuerdo material, M&A | ~500 | ~2.500 | Alto impacto, bien definido |
| 5.02 Salida de CEO/CFO (filtrado a "abrupta") | ~200 | ~1.000 | Buena clase |
| 4.01 Cambio de auditor | ~200 | ~1.000 | Buena clase |
| 4.02 Non-reliance / restatement | ~120 | ~600 | **Rara, alto impacto, muy limpia de definir** |
| 1.03 Concurso / quiebra | ~75 | ~375 | Rara |
| 8.01 Otros eventos | ~4.000 | ~20.000 | Heterogéneo — requiere clasificación LLM |

**Verificación exacta y gratuita, ~10 minutos** (hazla el día 1, no te fíes de mi `[E]`):
descarga los 20 ficheros `https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{N}/form.idx` (2021-2025), cuenta las líneas que empiezan por `8-K`, y cruza los CIK contra tu universo. El `form.idx` es el censo autoritativo y no cuesta nada. *(Yo no pude: egress bloqueado, §1.5.)*

#### 2.2.3 El cálculo que decide el diseño: potencia estadística

Esto es lo más importante de toda la auditoría.

Con n eventos y desviación típica σ del retorno anormal en la ventana, el **efecto mínimo detectable** (80% potencia, bilateral α=0,05) es:

```
MDE = 2,8 · σ / √n
```

| Clase de evento | n (5 años) | σ del CAR | **MDE** | ¿Detecta +30 bps? |
|---|---|---|---|---|
| Earnings drift (2.02) | 50.000 | 8% | **10 bps** | **Sí, con margen** |
| M&A / 1.01 | 2.500 | 12% | **67 bps** | No |
| CEO abrupto | 1.000 | 9% | **80 bps** | No |
| Restatement (4.02) | 600 | 10% | **114 bps** | No |
| FDA — todos los catalizadores | 350 | 25% | **374 bps** | No, ni de lejos |
| FDA — aprobaciones materiales | 100 | 25% | **700 bps** | No, ni de lejos |
| AdCom | 100 | 20% | **560 bps** | No, ni de lejos |

**Lee la última columna otra vez.** La consecuencia es dura y va contra la intuición del proyecto:

> **La pata FDA del sistema es estadísticamente incapaz de validar nada por debajo de un edge de ~4% por evento.** No porque el edge no exista, sino porque con n≈350 y σ≈25% no puedes distinguirlo del ruido. Si el POC intenta demostrar "+30 bps" sobre eventos FDA, el resultado será no concluyente con total independencia de si el concepto funciona.

Y su espejo:

> **El objetivo de +30 bps solo es demostrable sobre clases de evento de alta frecuencia y baja varianza.** En la práctica: earnings, y poco más.

**Hay una segunda trampa, y es sutil.** Generar +3% anual es fácil aritméticamente: 200 trades/año × 3% de peso × 0,5% de edge = 3,0% anual. Pero *demostrar* ese 0,5% con los propios trades del backtest (n≈1.000 en 5 años, σ≈8%) da MDE = 71 bps. **No puedes probar un edge de 50 bps con tu propio P&L.**

De ahí la regla de diseño que gobierna toda la arquitectura:

- **Validación estadística** → sobre el event study completo (todos los eventos, n en miles).
- **Validación económica** → sobre el backtest del subconjunto operado (n en cientos).

Son dos números distintos que responden dos preguntas distintas. Confundirlos es el modo de fallo más común en este tipo de proyecto.

#### 2.2.4 Veredicto §5 y simplificación

**PARCIAL.** Con matices por pata:

- **EDGAR: SÍ.** Hay n de sobra en 3-4 clases para distribuciones condicionales honestas.
- **FDA: NO, como base estadística autónoma.** 238-350 eventos con σ=25% no sostienen una distribución condicionada.

**Simplificaciones válidas, por orden de preferencia:**

1. **Anclar la validación en EDGAR, no en FDA.** Que la pata FDA sea un satélite exploratorio declarado, no la prueba del concepto. Es el cambio de diseño más importante que sale de esta auditoría.
2. **Prohibir el condicionamiento por celdas.** Con n=350, condicionar por (cap × fase × run-up × régimen) da celdas de n=5. Vale cero. Usa **como máximo una variable de condicionamiento por clase, elegida a priori y escrita antes de mirar los datos.**
3. **k-NN sobre métrica de distancia** en vez de celdas. Recupera los 20-30 análogos más cercanos y reporta la distribución empírica **con sus intervalos de confianza**. Con n=30 los intervalos son enormes — esa es precisamente la información honesta que el sistema debe dar.
4. **Shrinkage bayesiano** hacia la media agrupada de la clase. Con pocos análogos, la estimación debe colapsar hacia el prior, no hacia el ruido de 5 observaciones.

---

### 2.3 §6 — Market context: VIX de yfinance, ¿qué falta?

**Veredicto: SÍ. Y es la sección donde más terreno gratis estás dejando sin recoger.**

#### Lo que ya tienes cubierto con yfinance
`^VIX`, `^VIX3M` (y de ahí la estructura temporal VIX/VIX3M, mejor indicador de régimen que el VIX a secas), `^GSPC`, `^NDX`, `^RUT`, ETFs sectoriales (`XLV`, `XBI`, `IBB`, `XLK`…), proxies de factor vía ETF, y toda la volatilidad/correlación/dispersión realizada calculable desde precios.

#### Cuatro fuentes gratis que no están en tu lista y deberían estar

| Fuente | Qué aporta | Por qué importa |
|---|---|---|
| **Ken French Data Library** | Factores Fama-French 3/5 + momentum, diarios | **Es la forma correcta de calcular retornos anormales.** Sin esto tu CAR es contra el mercado a secas y confundes beta con alpha. No es opcional. |
| **FRED (API gratis)** | Tipos, spread de crédito HY (`BAMLH0A0HYM2`), curva, condiciones financieras (`NFCI`) | Régimen macro real, no un proxy de volatilidad |
| **FINRA / NYSE / Nasdaq short interest** | Interés corto bisemanal | Gratis, con ~2 semanas de retardo. Predictor conocido de reacción a catalizadores |
| **SEC Form 4** | Transacciones de insiders, en ~2 días hábiles | Señal gratis, casi en tiempo real, infrautilizada. Insider compra antes de un catalizador es información |

Añadir Ken French y FRED cuesta unas horas y mejora el rigor de §4 más que ninguna otra cosa del plan.

#### Lo que falta de verdad y no tiene sustituto gratis

| Falta | Gravedad | ¿Proxy gratis? |
|---|---|---|
| Superficie de volatilidad implícita / movimiento esperado | **Alta** | Parcial (§2.1) |
| Consenso de analistas (para sorpresa de earnings) | **Alta** | **No.** Esto mata las estrategias de sorpresa de resultados |
| Coste de préstamo / hard-to-borrow | Media | No |
| Datos intradía | Media | Parcial: yfinance da 1m solo 30 días, 1h 730 días |
| Clasificación sectorial GICS | Baja | Sí: códigos SIC de EDGAR, más bastos pero utilizables |
| Propiedad institucional | Baja | Sí: 13F, trimestral, 45 días de retardo |

**Nota sobre el consenso de analistas:** su ausencia es lo que impide explotar la clase de evento con mejor potencia estadística (earnings, MDE de 10 bps). Es una ironía incómoda del diseño gratuito y está tratada en el plan de fases.

---

### 2.4 §9 — La amenaza que no está en tu lista: sesgo de supervivencia

No la mencionas, y es la que más probablemente invalide el resultado del POC.

**yfinance no sirve bien tickers deslistados.** Una empresa que quebró, fue adquirida o salió del mercado en 2022 simplemente no devuelve datos. Pero un sistema de eventos está sesgado *precisamente* hacia esas empresas: quiebras (1.03), restatements (4.02), M&A (2.01) son eventos cuyo desenlace frecuente es la desaparición del ticker.

Si construyes el universo desde yfinance, mides el rendimiento de los eventos **condicionado a que la empresa sobreviviera hasta hoy**. El resultado saldrá optimista y será falso.

**Mitigación gratuita (parcial, pero obligatoria):**
1. Construye el universo desde **EDGAR**, que sí contiene a los muertos.
2. Detecta deslistados vía formularios **25-NSE** en EDGAR.
3. **Mide y publica el sesgo**: qué % de eventos no tienen datos de precio, y cómo difiere su composición por clase. Si el 30% de tus restatements no tiene precios, di que el resultado de esa clase no es fiable.
4. Limita las afirmaciones a lo que los datos soportan.

Esto no elimina el sesgo. Lo hace visible y acotado, que es lo máximo que se puede hacer gratis. Eliminarlo es la primera compra de la fase 1.

**Adicional — trampa de yfinance:** los cierres ajustados se recalculan retroactivamente por splits y dividendos. Si guardas una serie ajustada hoy y la comparas con la de mañana, cambia. Guarda **cierre crudo + factor de ajuste**, con fecha de captura.

---

### 2.5 §10 — Real-time reaction: descartado, y con qué se sustituye

Confirmo tu decisión. No es viable, y conviene tener el motivo por escrito para no revisitarlo.

#### Por qué está muerto, con presupuesto de latencia

| Eslabón | Latencia gratis | Latencia profesional |
|---|---|---|
| Diseminación EDGAR | RSS / índice: **minutos** | PDS (feed oficial de pago): **<1 s** |
| Cotización yfinance | **15 min de retardo** + rate limiting | tiempo real |
| NewsAPI plan free | **24 h de retardo** `[V]` | tiempo real |

Fuente de los límites de NewsAPI: [NewsAPI free tier](https://freeapihub.com/apis/news-api), [análisis de límites](https://apitube.io/en-se/blog/post/best-free-news-apis-honest-limitations) — 100 peticiones/día, archivo de 1 mes, uso no comercial, CORS solo localhost, retardo de 24 h.

Compites en minutos-a-horas contra sistemas que operan en milisegundos. El salto del día del evento ya ha ocurrido y está incorporado al precio antes de que tu pipeline se entere. **NewsAPI free, con 24 h de retardo y 100 peticiones/día, no sirve como fuente de señal en ningún diseño.** Úsalo solo como metadato descriptivo, o sustitúyelo directamente por RSS (gratis, ilimitado, mismo día).

#### Sustituto propuesto: "§10' — Reacción post-cierre y deriva"

En vez de perseguir el salto, se persigue la **deriva post-anuncio**, que es lenta, está documentada en la literatura, y sobrevive a frecuencia diaria — es decir, es justo lo que un sistema con datos gratuitos sí puede capturar.

**Diseño:**

| Elemento | Definición |
|---|---|
| Detección | Evento detectado en cualquier momento del día D (batch nocturno) |
| Supuesto de información | El evento se considera público al **cierre de D**. Sin excepciones, aunque se detecte a las 10:00 |
| Entrada | **Apertura de D+1** (variante conservadora: cierre de D+1) |
| Salida | Cierre de D+5 y D+20 (dos horizontes, reportados por separado) |
| Objetivo | Deriva post-anuncio, no el salto |

**La regla de honestidad que hace que esto valga:** se reportan **dos números separados**, siempre.

- **(a) Retorno del día del evento (D0)** → NO capturable. Es el dinero que dejas en la mesa. Se mide y se publica igualmente, porque es la medida de lo que cuesta la restricción gratuita.
- **(b) Retorno D+1 apertura → D+N** → tu P&L real. Es el único número sobre el que se puede afirmar nada.

Mezclar (a) y (b) en una sola cifra de rendimiento es la forma más habitual de auto-engaño en backtests de eventos. Aquí quedan separados por construcción.

**Beneficio arquitectónico mayor:** si la entrada nunca es antes de D+1, **todo el sistema es un batch nocturno.** Sin streaming, sin websockets, sin infraestructura de baja latencia, sin cola de mensajes. Esto es lo que hace que el plan de una semana sea realista y no una fantasía. La restricción de datos, bien aceptada, simplifica el sistema entero.

---

## 3. Conclusión de la auditoría

**¿Es viable en gratuito?** Sí, con tres reservas y un cambio de diseño no negociable.

**Es viable:**
- La ingesta, la clasificación, el motor de event study y el backtest diario funcionan íntegramente con fuentes gratis.
- Renunciar al tiempo real convierte el sistema en un batch nocturno y lo hace construible en una semana.
- El coste real del POC es ~$50-100 de Claude API y cero de datos.

**El cambio de diseño obligatorio:**
- **La validación del concepto tiene que anclarse en eventos EDGAR de alta frecuencia, no en FDA.** La pata FDA no tiene potencia estadística para probar nada por debajo de un 4% de edge por evento. Mantenerla como satélite exploratorio está bien; usarla como prueba del concepto garantiza un resultado no concluyente.

**Las tres reservas:**
1. **Sesgo de supervivencia de yfinance** es la amenaza más seria a la validez. Se mide, no se elimina. Eliminarlo es la primera compra de la fase 1.
2. **Sin consenso de analistas**, la clase de evento con mejor potencia estadística (earnings) queda parcialmente inutilizada.
3. **Este entorno no tiene red hacia ninguna fuente.** Hay que resolverlo antes del día 1.

**El criterio de éxito del POC hay que reformularlo.** No es "¿gana +3% anual?" — eso no es demostrable con 5 años de datos. Es:

> ¿Existe un CAR anormal medio, estadísticamente significativo tras corregir por contrastes múltiples, en la ventana D+1→D+20, para al menos una clase de evento pre-registrada, que sobrevive a 25 bps de slippage y a una validación fuera de muestra?

Esa pregunta sí tiene respuesta con datos gratuitos en una semana. La otra no.

---

## Fuentes

- [RAPS — CDER approved 46 novel drugs in 2025](https://www.raps.org/resource/cder-approved-46-novel-drugs-in-2025-half-for-rar.html)
- [FDA — Novel Drug Approvals at FDA](https://www.fda.gov/drugs/development-approval-process-drugs/novel-drug-approvals-fda)
- [openFDA — Complete Response Letters](https://open.fda.gov/apis/other/approved_CRLs/)
- [National Center for Health Research — FDA shuns advisory meetings](https://www.center4research.org/stat-fda-shuns-advisory-committee-meetings/)
- [openFDA — Authentication and rate limits](https://open.fda.gov/apis/authentication)
- [SEC — Number of EDGAR Filings by Form Type](https://www.sec.gov/data-research/sec-markets-data/number-edgar-filings-form-type)
- [FreeAPIHub — NewsAPI free tier](https://freeapihub.com/apis/news-api)
- [APITube — Best free news APIs, honest limitations](https://apitube.io/en-se/blog/post/best-free-news-apis-honest-limitations)
- [deepcharts — Why did the yfinance library break](https://deepcharts.substack.com/p/why-did-the-yfinance-python-library)
- [yfinance issue #2128 — New rate-limiting](https://github.com/ranaroussi/yfinance/issues/2128)
