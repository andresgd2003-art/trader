"""
engine/daily_mode.py
=====================
Sistema de Rotación Diaria de Propuestas A/B/C.

LÓGICA:
  El modo activo alterna cada día del año:
    Día 0 (1-Jan) → A (RegimeManager / market-environment-analysis)
    Día 1 (2-Jan) → B (NewsRiskFilter / market-news-analyst)
    Día 2 (3-Jan) → C (StockScorer / us-stock-analysis)
    Día 3 (4-Jan) → A ... y así sucesivamente

OVERRIDE MANUAL:
  Configurar variable de entorno FORCE_MODE=A|B|C para fijar el modo.

ESTADO GLOBAL:
  _ACTIVE_MODE y _MODE_META son accesibles mediante get_active_mode().
  El modo se persiste en /app/data/.trading_mode para reintentos sin pérdida.

DESCRIPCIÓN DE CADA MODO:
  A — Árbitro Global de Régimen      : activa/desactiva bots según SPY/SMA200/VIX
  B — Filtro de Noticias Pre-Entrada : bloquea órdenes si hay riesgo fundamental
  C — Scoring Dinámico de Candidatos : elige acciones con mejor score cuantitativo
"""
import os
import logging
from datetime import datetime, date, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constantes ──────────────────────────────────────────────
MODE_A = "A"
MODE_B = "B"
MODE_C = "C"
MODES  = [MODE_A, MODE_B, MODE_C]

MODE_DESCRIPTIONS = {
    MODE_A: {
        "name":   "Árbitro de Régimen",
        "skill":  "market-environment-analysis",
        "short":  "Régimen SPY/VIX activa/desactiva bots según Bull/Bear/Chop",
        "emoji":  "🌡️",
    },
    MODE_B: {
        "name":   "Filtro de Noticias",
        "skill":  "market-news-analyst",
        "short":  "Filtra entradas si hay noticias de riesgo fundamental pre-trade",
        "emoji":  "📰",
    },
    MODE_C: {
        "name":   "Scoring Dinámico",
        "skill":  "us-stock-analysis",
        "short":  "Selecciona candidatos por momentum real en vez de universo fijo",
        "emoji":  "🎯",
    },
}

# ── Estado Global ────────────────────────────────────────────
_ACTIVE_MODE: str = MODE_A
_MODE_META: dict = {}
# [P4 FIX - 2026-04-15] Mapeo explícito a /opt/trader/data para prevenir Split-Brain
_PERSIST_PATH: Path = Path(os.environ.get("DATA_PATH", "/opt/trader/data")) / ".trading_mode"


# ── Funciones de acceso ──────────────────────────────────────

def get_active_mode() -> str:
    """Retorna la letra del modo activo: 'A', 'B' o 'C'."""
    return _ACTIVE_MODE


def get_mode_meta() -> dict:
    """Retorna metadata completa del estado actual del modo."""
    return _MODE_META


def get_mode_label() -> str:
    """Retorna una etiqueta corta para incluir en client_order_id: 'mA', 'mB', 'mC'."""
    return f"m{_ACTIVE_MODE}"


def get_next_schedule() -> list:
    """Retorna los próximos 7 días con su modo asignado."""
    today = date.today()
    schedule = []
    for i in range(7):
        from datetime import timedelta
        d = today + timedelta(days=i)
        mode = MODES[d.timetuple().tm_yday % 3]
        schedule.append({"date": d.isoformat(), "mode": mode, "desc": MODE_DESCRIPTIONS[mode]["name"]})
    return schedule


# ── Lógica de determinación del modo ────────────────────────

def _determine_mode() -> str:
    """Determina el modo según día del año (modulo 3) o FORCE_MODE env var."""
    forced = os.environ.get("FORCE_MODE", "").strip().upper()
    if forced in MODES:
        logger.info(f"[DailyMode] ⚠️  FORCE_MODE={forced} activo (override manual).")
        return forced

    day_of_year = date.today().timetuple().tm_yday  # 1-365
    mode = MODES[(day_of_year - 1) % 3]
    return mode


def _load_persisted_mode() -> str | None:
    """Carga el modo guardado en disco (para reintentos tras reinicio)."""
    try:
        if _PERSIST_PATH.exists():
            data = _PERSIST_PATH.read_text().strip()
            saved_date, saved_mode = data.split("|")
            if saved_date == date.today().isoformat() and saved_mode in MODES:
                return saved_mode
    except Exception:
        pass
    return None


def _persist_mode(mode: str) -> None:
    """Guarda el modo activo en disco junto con la fecha de hoy."""
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(f"{date.today().isoformat()}|{mode}")
    except Exception as e:
        logger.warning(f"[DailyMode] No pudo persistir modo: {e}")


def _log_mode_change(mode: str) -> None:
    """Registra el cambio de modo en el log CSV histórico."""
    try:
        # [P4 FIX - 2026-04-15] Mapeo explícito a /opt/trader/data
        log_path = Path(os.environ.get("DATA_PATH", "/opt/trader/data")) / "mode_log.csv"
        exists = log_path.exists()
        with open(log_path, "a", encoding="utf-8") as f:
            if not exists:
                f.write("Fecha,Modo,Skill,Nombre\n")
            f.write(f"{datetime.now(timezone.utc).isoformat()},{mode},"
                    f"{MODE_DESCRIPTIONS[mode]['skill']},{MODE_DESCRIPTIONS[mode]['name']}\n")
    except Exception as e:
        logger.warning(f"[DailyMode] No pudo escribir mode_log.csv: {e}")


# ── Inicialización ───────────────────────────────────────────

class DailyModeManager:
    """
    Gestor del modo diario A/B/C.
    Se instancia una sola vez en api_server.py al arranque.
    """

    def __init__(self):
        self.refresh()

    def refresh(self) -> str:
        """Recalcula y actualiza el modo activo. Llamar al inicio de cada día."""
        global _ACTIVE_MODE, _MODE_META

        # Intentar cargar modo persistido (mismo día)
        persisted = _load_persisted_mode()
        new_mode = persisted or _determine_mode()

        if new_mode != _ACTIVE_MODE or not _MODE_META:
            _ACTIVE_MODE = new_mode
            _persist_mode(new_mode)
            _log_mode_change(new_mode)

        desc = MODE_DESCRIPTIONS[_ACTIVE_MODE]
        _MODE_META = {
            "mode":        _ACTIVE_MODE,
            "label":       f"m{_ACTIVE_MODE}",
            "name":        desc["name"],
            "skill":       desc["skill"],
            "short":       desc["short"],
            "emoji":       desc["emoji"],
            "date":        date.today().isoformat(),
            "schedule":    get_next_schedule(),
            "forced":      bool(os.environ.get("FORCE_MODE")),
        }

        logger.info(
            f"[DailyMode] {desc['emoji']} Modo activo: {_ACTIVE_MODE} — {desc['name']} "
            f"| Skill: {desc['skill']}"
        )
        return _ACTIVE_MODE

    def is_mode(self, mode: str) -> bool:
        """Verifica si el modo actual es el especificado."""
        return _ACTIVE_MODE == mode.upper()
