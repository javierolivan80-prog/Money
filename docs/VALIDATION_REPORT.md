# VALIDATION_REPORT.md

_Generado automáticamente por `pipeline/validation/report.py` el 2026-09-12T21:58:16._

**Advertencia de honestidad, léela antes que el resto del documento**: los
números de este reporte salen de datos SINTÉTICOS generados en este sandbox
de desarrollo (ver `RUNBOOK.md` — ningún scraper de EDGAR/FDA/yfinance real
se ha ejecutado nunca desde aquí, por bloqueo de red de la organización).
El código que produjo cada número está probado con 269 tests contra
Postgres real, así que el MECANISMO es de fiar; el VEREDICTO concreto de
este documento (GREENLIGHT/YELLOWLIGHT/REDLIGHT) NO debe tomarse como una
recomendación real de inversión hasta que se regenere este mismo reporte
con datos reales (`RUNBOOK.md` §1-§3.11 explica cómo).

## Executive Summary

Este documento responde 4 preguntas, en este orden, porque cada una
presupone que la anterior ya se contestó que sí (`AUDIT_LEAN.md` §2.2):
1. ¿El evento en sí mueve el precio de forma no aleatoria? (Event Study)
2. Si es así, ¿una estrategia de trading sobre él hubiera ganado dinero?
   (Backtest)
3. ¿El modelo sabe cuándo confiar en sí mismo? (Calibración)
4. ¿El resultado es frágil a supuestos razonables? (Sensibilidad)

**Veredicto**: **YELLOWLIGHT — CONDITIONALLY VIABLE** (versión recomendada: AGGRESSIVE).
Invierte capital mínimo en la fuente de datos más barata. Paper trade 3 meses más antes de live.

## PARTE 1 — Event Study (validez del concepto)

Sobre `car_results` (ventana de 20 días), no sobre los trades del backtest —
es la pregunta "¿existe un edge?", con n en la escala de todos los eventos
analizados, no solo los operados (`AUDIT_LEAN.md` §2.2.3).

| Event Type | n | Median Return | σ | MDE | p-value | Conclusion |
|---|---|---|---|---|---|---|
| 8K_1.01_MATERIAL_AGREEMENT | 18 | -0.51% | 5.2% | 3 bps | 0.6779 | ✗ No significativo (p=0.6779 >= 0.05) — MDE=3 bps con esta n, podría ser ruido o un efecto real más pequeño que el MDE |
| 8K_2.02_EARNINGS | 17 | -0.43% | 6.5% | 4 bps | 0.6197 | ✗ No significativo (p=0.6197 >= 0.05) — MDE=4 bps con esta n, podría ser ruido o un efecto real más pequeño que el MDE |
| 8K_5.02_OFFICER_CHANGE | 18 | +1.68% | 5.4% | 4 bps | 0.4559 | ✗ No significativo (p=0.4559 >= 0.05) — MDE=4 bps con esta n, podría ser ruido o un efecto real más pequeño que el MDE |

MDE = 2.8·σ/√n (80% potencia, α=0.05 bilateral) — misma fórmula que
`AUDIT_LEAN.md` §2.2.3. Una clase con p-value >= 0.05 no está descartada:
puede tener un efecto real más pequeño que el MDE actual, no cero.

## PARTE 2 — Backtesting (viabilidad operativa)

| Versión | Total Return | Sharpe | Max DD | Win Rate | N | Rec |
|---|---|---|---|---|---|---|
| CONSERVATIVE | -0.4% | -4.02 | 0.6% | 39.7% | 58 | NO |
| BALANCED | -1.9% | -3.48 | 2.0% | 37.5% | 56 | NO |
| AGGRESSIVE | 5.2% | 0.87 | 5.7% | 60.0% | 25 | NO |

Reporte de sesgos (sobre todo el universo, no por versión): 8/75 tickers deslistados (10.7% posible sesgo de supervivencia) · 1802/19463 filas de precio con gap (9.3%).

## PARTE 3 — Calibración (confiabilidad del modelo)

Ver el reporte completo en `/calibration` del dashboard (curva de
calibración, Brier score, ECE por versión) — aquí, el resumen que pide el
spec:

| Versión | Calibration score (Fase 3) | Meets target (>0.6) |
|---|---|---|
| CONSERVATIVE | -0.10 | ✗ |
| BALANCED | -0.61 | ✗ |
| AGGRESSIVE | 0.46 | ✗ |

## PARTE 4 — Sesgo y limitaciones

1. **Survivorship bias**: ver PARTE 2 arriba — deslistados SÍ se capturan
   (`universe.is_delisted_flag`), no se excluyen del universo simulado.
2. **Data quality**: gaps de precio flageados, no interpolados (ver
   `yfinance_backfill.py` — es información, no un bug).
3. **Look-ahead checks**:
   - **CONSERVATIVE**: ✓ sin violaciones
   - **BALANCED**: ✓ sin violaciones
   - **AGGRESSIVE**: ✓ sin violaciones
4. **Walk-forward / estabilidad temporal**: partición en dos mitades
   cronológicas (`portfolio_validation.compute_temporal_stability_report`)
   — no es un walk-forward con reentrenamiento real (este proyecto no
   reentrena ningún modelo; Bull/Bear/Judge son prompts fijos, no pesos
   entrenados sobre datos propios).
5. **Regime dependency**: ver la tabla de sensibilidad (PARTE 5) — split
   alto/bajo VIX en la entrada, la varianza REAL observada en los datos
   disponibles (no una resimulación estocástica bajo un choque
   hipotético — este proyecto no tiene un modelo de precios, ver
   `sensitivity.py`).

## PARTE 5 — Sensibilidad

| Scenario | Conservative Return | Aggressive Return | Impact |
|---|---|---|---|
| Baseline | -0.4% | 5.2% | — |
| Comisiones +0.1%/trade | -0.5% | 4.8% | -0.16pp vs baseline (Conservative) |
| Spread +0.2% | -0.7% | 4.4% | -0.33pp vs baseline (Conservative) |
| Entrada D+2 en vez de D+1 (latencia) | -0.2% | 6.0% | +0.17pp vs baseline (Conservative) |
| Modelo reduce confidence 20% | -0.4% | 5.2% | +0.00pp vs baseline (Conservative) |
| Régimen alto-VIX (proxy de 'VIX sube', ver sensitivity.py) | -0.2% | 2.5% | +0.16pp vs baseline (Conservative) |
| Régimen bajo-VIX | -0.2% | 2.7% | +0.20pp vs baseline (Conservative) |

## PARTE 6 — Decisión de inversión

- **CONSERVATIVE**: REDLIGHT — NOT READY — sharpe=-4.02 < 0.7; win_rate=0.40 < 0.5; calibración=-0.10 < 0.4
- **BALANCED**: REDLIGHT — NOT READY — sharpe=-3.48 < 0.7; win_rate=0.38 < 0.5; calibración=-0.61 < 0.4
- **AGGRESSIVE**: YELLOWLIGHT — CONDITIONALLY VIABLE — No cumple todos los criterios de GREENLIGHT, pero tampoco dispara REDLIGHT: sharpe=0.87, calibración=0.46, n=25 (mínimo 300), walk_forward_passed=False

**Veredicto global: YELLOWLIGHT — CONDITIONALLY VIABLE** (la mejor de las 3 versiones evaluadas, empates a favor de Conservative).

## PARTE 7 — Next steps

Si el veredicto es GREENLIGHT o YELLOWLIGHT:
1. Dato a comprar primero: Tiingo (tickers deslistados, ~$15/mes) — cierra
   el sesgo de supervivencia real (`AUDIT_LEAN.md` §3).
2. Versión a activar en vivo: AGGRESSIVE (la que superó el veredicto).
3. Monitoreo primeros 3 meses: revisar `/week` (paper trading) cada semana,
   `/calibration` cada mes — un cambio de signo en la expectancy o una
   divergencia de calibración >15pp son las señales de alerta ya
   implementadas (`portfolio_validation.py`,
   `paper_trading/analysis.py:compute_alerts`).
4. Escalar tamaño de posición: solo tras 3 meses de paper trading sin
   alerts de "posible overfitting" (2+ pérdidas consecutivas).
5. Shut-down trigger: cualquier violación anti-look-ahead detectada en una
   corrida real (no debería pasar nunca — es un bug, no una señal de
   mercado), o calibración cayendo por debajo de 0.3 durante 2 meses
   seguidos.

Si el veredicto es REDLIGHT: no proceder — revisar qué versión/clase de
evento específica falló (PARTE 1-2 arriba) antes de repetir este análisis.

## Appendix: Top 10 mejor / peor predichos (todas las versiones)

| Versión | Ticker | Clase | Salida | PnL % |
|---|---|---|---|---|
| AGGRESSIVE | HIST44 | 5.02_OFFICER_CHANGE | MAX_HOLDING | +13.98% |
| AGGRESSIVE | HIST35 | 5.02_OFFICER_CHANGE | MAX_HOLDING | +11.62% |
| AGGRESSIVE | HIST49 | 1.01_MATERIAL_AGREEMENT | MAX_HOLDING | +9.85% |
| AGGRESSIVE | HIST20 | 5.02_OFFICER_CHANGE | MAX_HOLDING | +8.79% |
| AGGRESSIVE | HIST67 | 1.01_MATERIAL_AGREEMENT | MAX_HOLDING | +7.20% |
| AGGRESSIVE | HIST37 | 1.01_MATERIAL_AGREEMENT | MAX_HOLDING | +7.03% |
| BALANCED | HIST30 | 2.02_EARNINGS | MAX_HOLDING | +5.85% |
| AGGRESSIVE | HIST30 | 2.02_EARNINGS | MAX_HOLDING | +5.85% |
| AGGRESSIVE | WEEK2 | 2.02_EARNINGS | MAX_HOLDING | +5.70% |
| AGGRESSIVE | HIST13 | 1.01_MATERIAL_AGREEMENT | MAX_HOLDING | +4.17% |
| AGGRESSIVE | HIST51 | 2.02_EARNINGS | STOP_LOSS | -5.10% |
| BALANCED | HIST17 | 5.02_OFFICER_CHANGE | STOP_LOSS | -5.10% |
| BALANCED | HIST26 | 5.02_OFFICER_CHANGE | STOP_LOSS | -5.10% |
| BALANCED | HIST45 | 2.02_EARNINGS | STOP_LOSS | -5.10% |
| BALANCED | HIST59 | 5.02_OFFICER_CHANGE | STOP_LOSS | -5.10% |
| AGGRESSIVE | HIST3 | 2.02_EARNINGS | STOP_LOSS | -5.10% |
| AGGRESSIVE | HIST7 | 1.01_MATERIAL_AGREEMENT | STOP_LOSS | -5.10% |
| AGGRESSIVE | HIST10 | 1.01_MATERIAL_AGREEMENT | STOP_LOSS | -5.10% |
| AGGRESSIVE | HIST17 | 5.02_OFFICER_CHANGE | STOP_LOSS | -5.10% |
| AGGRESSIVE | HIST29 | 5.02_OFFICER_CHANGE | STOP_LOSS | -5.10% |

---
_Datos completos de todos los trades: ver el CSV exportado junto a este
documento (`pipeline/validation/report.py:export_trades_csv`)._
