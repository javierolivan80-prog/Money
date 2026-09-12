# DATA_REQUIREMENTS_PHASED.md — Plan de evolución de datos por fases

Fecha: 2026-09-12
Alcance: TAREA 4
Condición de activación: **el POC gratuito muestra edge** — CAR OOS significativo en D+1→D+20 que sobrevive a 25 bps de slippage (`ARCHITECTURE_LEAN.md` §11).

---

## 0. El principio de ordenación

Las fuentes no se ordenan por lo atractivas que son, sino por **ROI de información**:

```
ROI = (edge desbloqueado o riesgo eliminado) / (coste anual + esfuerzo de integración)
```

De ahí salen dos consecuencias que van contra el instinto:

**1. La primera compra no añade señal. Elimina un sesgo.** Suena decepcionante y es lo correcto: el dato más valioso es el que te dice si lo que ya mediste era real. Comprar señal encima de un resultado sesgado es multiplicar un error.

**2. El feed de baja latencia se compra tarde, o nunca.** Es lo que todo el mundo quiere comprar primero. Si tu edge es una deriva de 5-20 días, la latencia vale exactamente cero. Comprarla es pagar por un problema que no tienes.

---

## 1. Fase 0 — POC (ya en curso)

| Concepto | Coste |
|---|---|
| Datos | **$0** |
| Claude API (backfill Haiku 4.5 + Batch) | **$47-94** una vez |
| Claude API (incremental) | céntimos/día |
| **Total año 1** | **< $150** |

**Salida:** una respuesta a la pregunta del `ARCHITECTURE_LEAN.md` §1, con intervalos de confianza y el MDE por clase de evento.

---

## 2. Fase 1 — Validar que el resultado es real

**Gasto: $500 - $1.500/año. Se ejecuta solo si el POC muestra edge.**

### 1.1 Datos de precio sin sesgo de supervivencia `PRIORIDAD MÁXIMA`

| Opción | Coste aprox. | Notas |
|---|---|---|
| Norgate Data | ~$500-1.000/año | Incluye deslistados, orientado a backtesting |
| Sharadar SEP+SF1 (Nasdaq Data Link) | ~$1.200/año | Precios + fundamentales, con deslistados |
| CRSP vía WRDS | $0-2.000 | Solo si tienes vínculo académico. Es el estándar de oro |

**Por qué va primero:** no añade ni una señal nueva. Te dice si el edge que mediste en la fase 0 existía o era un artefacto de que yfinance no sirve los tickers muertos (`AUDIT_LEAN.md` §2.4). Y tu sistema está sesgado justo hacia esos: quiebras, restatements y M&A son eventos cuyo desenlace habitual es la desaparición del ticker.

**Efecto esperado: puede mover el edge medido en ±50-100 bps, y puede matar la estrategia entera.** Eso *es* el valor. $1.000 para no desplegar capital sobre un artefacto es el mejor ratio de todo el documento.

**Regla:** si el edge no sobrevive a datos sin sesgo de supervivencia, **el proyecto para aquí.** No se compra nada más.

### 1.2 Short interest histórico limpio `opcional, barato`
FINRA lo publica gratis pero el histórico ordenado da trabajo. ~$200-400/año si prefieres comprarlo hecho. Baja prioridad.

**Puerta de salida de la fase 1:** el edge sobrevive a datos sin supervivencia, fuera de muestra, con 25 bps de coste. Si no → parar.

---

## 3. Fase 2 — Afilar la señal

**Gasto acumulado: $3.000 - $5.000/año.**

### 2.1 Datos de opciones / movimiento implícito `MAYOR GANANCIA DE ALPHA`

| Opción | Coste aprox. |
|---|---|
| Polygon.io Options Starter | $29-199/mes |
| ORATS | $100-300/mes |
| CBOE DataShop (a la carta, histórico) | variable, compra puntual |

**Qué desbloquea:** el término que falta en la ecuación del edge. Ahora mismo mides `tu_previsión`. Con el movimiento implícito mides `tu_previsión − previsión_del_mercado`, que es lo que realmente determina si ganas dinero (`AUDIT_LEAN.md` §2.1).

**Efecto esperado — y ojo al mecanismo, porque no es el que parece:** el valor no está en operar más, sino en **operar menos**. Te permite descartar los eventos donde el movimiento implícito ya descuenta tu previsión. Filtrar la mitad de los trades sin edge puede duplicar aproximadamente el edge por operación, aunque el retorno anual suba menos que eso porque haces menos operaciones. Mejora el Sharpe más que el retorno.

**Requisito previo:** solo tiene sentido sobre clases de evento con catalizador programado (fechas PDUFA, resultados, AdComs). Si tu edge de fase 1 está en eventos no anticipados, **sáltate esta compra** — no hay opciones que descuenten un evento que nadie esperaba.

### 2.2 Consenso de analistas `solo si el edge está en earnings`

| Opción | Coste aprox. |
|---|---|
| Sharadar / Nasdaq Data Link | ~$100-300/mes |
| Benzinga | ~$200-500/mes |

**Qué desbloquea:** la sorpresa de resultados, que es el conductor mejor documentado de la deriva post-anuncio. Es también la clase con mejor potencia estadística de toda la auditoría (MDE de 10 bps, `AUDIT_LEAN.md` §2.2.3).

**Condicional estricto:** cómprala **solo** si el POC muestra que los eventos 2.02 están entre tus mejores clases. Si tu edge está en restatements o M&A, esto no aporta nada.

**Puerta de salida de la fase 2:** el edge por operación mejora de forma medible con los nuevos datos, en una evaluación fuera de muestra nueva. Si no mejora, no escales.

---

## 4. Fase 3 — Escalar

**Gasto acumulado: $12.000 - $20.000/año. Solo con capital desplegado que lo justifique.**

### 3.1 Barras intradía
Polygon.io ($29-199/mes) o Databento (pago por uso). **Para calidad de ejecución, no para señal.** Se compra cuando el capital desplegado hace que el slippage importe — orientativamente por encima de ~$100k. Por debajo, entrar en la apertura es suficiente.

### 3.2 Feed de filings de baja latencia
SEC PDS (~$3.000-5.000/año) o un proveedor con websocket (sec-api.io, ~$50-200/mes).

**Cómprese solo si se cumple una condición concreta:** que el POC demuestre que el edge está concentrado en las primeras horas. Con el diseño de deriva D+1→D+20 de `ARCHITECTURE_LEAN.md`, **esta compra vale cero.** Está aquí por completitud y porque es la tentación más habitual, no porque la recomiende.

### 3.3 Noticias profesionales / NLP
RavenPack, Bloomberg: $20.000-100.000+/año. **Fuera de escala para este proyecto.** Listado para cerrar la pregunta, no como recomendación.

---

## 5. Resumen económico

| Fase | Coste anual | Qué compra | Disparador |
|---|---|---|---|
| **0** | < $150 | La respuesta a "¿predice algo?" | — |
| **1** | $500-1.500 | La confianza de que la respuesta es real | POC muestra edge |
| **2** | $3.000-5.000 | La previsión del mercado, para restarla de la tuya | Fase 1 sobrevive |
| **3** | $12.000-20.000 | Calidad de ejecución a escala | Capital desplegado lo justifica |

### ¿Qué retorno haría falta para que cada fase se pague?

| Fase | Coste anual | Capital para que cueste <1% anual |
|---|---|---|
| 1 | $1.500 | $150.000 |
| 2 | $5.000 | $500.000 |
| 3 | $20.000 | $2.000.000 |

**Esta tabla es la que gobierna el plan.** Con el objetivo de +3% anual, el coste de datos tiene que quedarse muy por debajo de esa cifra o se come el edge entero. Con $100.000 de capital, la fase 2 ($5.000/año) consume el 5% anual — es decir, **más que el edge objetivo completo**. La fase 2 no tiene sentido económico por debajo de aproximadamente $500.000 de capital, por muy bueno que sea el dato.

Dicho de otro modo: el techo de este proyecto no lo pone la calidad de los datos, lo pone el capital. Conviene saberlo antes de gastar, no después.

---

## 6. Reglas de gasto

1. **Ninguna fase se compra sin que la anterior haya pasado su puerta de salida fuera de muestra.**
2. **Una fuente nueva, una evaluación OOS nueva.** Nada de reutilizar el holdout ya gastado: se consume una sola vez (`ARCHITECTURE_LEAN.md` §8, T6).
3. **Prueba antes de suscribir.** Casi todos dan trial o histórico puntual. Valida sobre una muestra antes del contrato anual.
4. **Presupuesto de datos ≤ 20% del edge esperado en euros.** Si esperas $9.000 al año ($300k al 3%), el techo de datos es $1.800 — es decir, la fase 1 y nada más.
5. **Si una fase no mejora el edge de forma medible, se cancela la suscripción.** Sin excepciones sentimentales.

---

## 7. Lo que no se compra nunca

| Fuente | Por qué no |
|---|---|
| Terminal Bloomberg ($24k/año) | No aporta nada automatizable que no tengas más barato |
| Feed de Reuters/Refinitiv | Escala institucional |
| Datos alternativos (satélite, tarjetas de crédito) | Escala institucional, y sin relación con eventos regulatorios |
| Cualquier cosa con mínimo anual > 20% del edge esperado | Regla 4 |

---

## 8. Qué pasa si el POC no muestra edge

El resultado más probable, según los MDE de la auditoría, es **"no concluyente"** en la pata FDA. Conviene distinguirlo bien, porque cada caso lleva a un sitio distinto:

| Diagnóstico | Lectura | Acción |
|---|---|---|
| MDE > edge plausible | Faltó **n**, no faltó edge | Cambiar a clases de evento con más n. **Gasto: $0** |
| IC estrecho centrado en cero | No hay edge ahí | Descartar la clase. Resultado válido y útil |
| Edge presente pero muere a 25 bps | Real pero no capturable retail | Replantear horizonte o universo. **No comprar datos** |
| T1 o T2 fallan | El sistema no mide lo que dice | Arreglar el motor. No interpretar nada aún |

**En ninguno de esos cuatro casos la respuesta es comprar datos.** Datos pagos arreglan sesgo y falta de información — no arreglan falta de muestra ni un edge que no existe. Esa es la confusión que hace que estos proyectos gasten dinero antes de haber aprendido nada.
