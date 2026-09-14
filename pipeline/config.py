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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # requerido para analyze/*
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
