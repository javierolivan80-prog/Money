"""config.py — configuración centralizada del pipeline.

Todo lo que necesita un secreto o vía de red vive aquí, léido de entorno.
Nada se hardcodea. Ver RUNBOOK.md para qué variables hay que definir y dónde.
"""
import os

# --- Base de datos ---
# Postgres alojado (Neon/Supabase free tier recomendado — ver RUNBOOK.md).
# Este pipeline se ejecuta en GitHub Actions (con salida a internet completa),
# NO en el sandbox de desarrollo, que tiene el egress bloqueado a EDGAR/FDA/
# Yahoo Finance/Ken French por política de la organización (AUDIT_LEAN.md §1.5).
_db_url = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/money_poc")
DATABASE_URL = _db_url.strip() if _db_url else ""

# --- Anthropic ---
_anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
# .strip(): mismo motivo que DATABASE_URL más arriba. Un secreto de GitHub
# pegado con un salto de línea al final llega aquí intacto, y al mandarlo como
# cabecera HTTP (Authorization / x-api-key) la librería lo rechaza:
#   httpx2.LocalProtocolError: Illegal header value b'***\n'
# Un salto de línea en una cabecera es el vector clásico de inyección de
# cabeceras HTTP, así que el rechazo es correcto — lo que hay que arreglar es
# no mandarlo sucio. Sin este strip, el mensaje de "falta ANTHROPIC_API_KEY"
# de más abajo no salta (la variable SÍ existe), y el fallo real queda
# enterrado dentro de las tripas del SDK.
ANTHROPIC_API_KEY = _anthropic_key.strip() if _anthropic_key else None
CLASSIFIER_MODEL = "claude-haiku-4-5"
ANALYZER_MODEL = "claude-haiku-4-5"  # Bull/Bear (Etapas 3-4) — "rápido", pedido por el spec
# Judge (Etapa 5): "mejor reasoning" — el spec de Fase 2 nombra Sonnet 4.6
# explícitamente, así que se usa ese ID en vez de la generación más reciente
# disponible (claude-sonnet-5). Ver adversarial_analyzer.py.
JUDGE_MODEL = "claude-sonnet-4-6"

# --- EDGAR ---
# La SEC exige un User-Agent identificable con contacto real. No es opcional:
# sin esto, EDGAR devuelve 403. https://www.sec.gov/os/webmaster-faq#developers
EDGAR_USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "Money-POC-Research contact@example.com"
)
EDGAR_RATE_LIMIT_PER_SEC = 8  # la SEC pide <=10 req/s; 8 deja margen
EDGAR_BASE = "https://www.sec.gov"

# --- Universo invertible (ARCHITECTURE_LEAN.md §10) ---
MIN_PRICE_USD = 5.0
MIN_MARKET_CAP_USD = 300_000_000
MIN_ADV_USD = 1_000_000
# Un precio más viejo que esto no cuenta para decidir si la empresa es
# invertible HOY (deslistadas, tickers que yfinance dejó de servir...).
MAX_PRICE_STALENESS_DAYS = 10


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int | None) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else default


# --- Cola del análisis con IA (Bull/Bear/Judge) ---
# Qué empresas pasan por la IA. Por defecto, el universo invertible entero
# (>= MIN_MARKET_CAP_USD). Para centrarse en empresas grandes, subirlo desde
# un secreto/variable del workflow, sin tocar código — p. ej. 10000000000
# (10.000 M$, "large caps").
ANALYSIS_MIN_MARKET_CAP_USD = _env_float("ANALYSIS_MIN_MARKET_CAP_USD", MIN_MARKET_CAP_USD)
# Tope de eventos analizados por corrida: es un tope de GASTO. Con los precios
# de la Batch API (-50%), un evento cuesta ~0,011 $ (2 llamadas Haiku 4.5 +
# 1 Sonnet 4.6 sobre ~8.000 caracteres de filing): 500 eventos ~ 5,5 $/corrida.
# Vacío o 0 = sin tope.
ANALYSIS_MAX_EVENTS_PER_RUN = _env_int("ANALYSIS_MAX_EVENTS_PER_RUN", 500) or None
ANALYSIS_EST_COST_PER_EVENT_USD = 0.011

# --- Ventanas de evento (ARCHITECTURE_LEAN.md §3, §5) ---
ESTIMATION_WINDOW_DAYS = (-250, -30)
EVENT_WINDOWS_DAYS = [5, 20]

# --- Periodo del POC ---
BACKTEST_START = "2021-01-01"
BACKTEST_END = "2025-12-31"
# Split OOS (ARCHITECTURE_LEAN.md §9, T6): ajustar SOLO en IN_SAMPLE, evaluar
# UNA VEZ en OOS. No se debe ejecutar el backtest sobre OOS más de una vez.
IN_SAMPLE_END = "2023-12-31"
OOS_START = "2024-01-01"

# --- Costes de transacción (T7: barrido de sensibilidad) ---
SLIPPAGE_BPS_SWEEP = [0, 10, 25, 50]
