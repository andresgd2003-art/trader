"""
engine/portfolio_manager.py
============================
PortfolioManager — Gestor de riesgo global para el Equities Engine.
Refactorizado para Cuentas CASH (T+1 Settled Cash).

REGLAS DE SEGURIDAD FINANCIERA:
  1. Cash Account: Solo operamos con settled_cash para evitar GFV.
  2. Max position: $20.00 USD por trade (escalabilidad para $500).
  3. Max drawdown: si el equity cae >10% del ATH → pausa total + liquida.
"""

import os
import logging
from alpaca.trading.client import TradingClient
from engine.notifier import TelegramNotifier
from datetime import datetime
from dotenv import load_dotenv

# Cargar .env con RUTA ABSOLUTA (Requisito PROMPT 18)
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
load_dotenv(env_path)

logger = logging.getLogger(__name__)

# Estado global para el dashboard
_PORTFOLIO_STATUS: dict = {
    "equity": 0.0,
    "settled_cash": 0.0,
    "ath": 0.0,
    "drawdown_pct": 0.0,
    "is_halted": False,
    "halt_reason": None,
    "last_check": None,
}

def get_portfolio_status() -> dict:
    return _PORTFOLIO_STATUS

class PortfolioManager:
    """
    Gestor de riesgo del portfolio enfocado en liquidéz asentada.
    """

    MAX_DRAWDOWN_PCT = 0.10   # 10% caída desde ATH → halt
    MAX_POSITION_USD = 20.0   # Ajustado para cuenta de $500

    # Mínimo 1h antes de evaluar resume; alerta Telegram si supera 48h en halt
    MIN_HALT_SECS_BEFORE_RESUME = 3600
    ALERT_HALT_SECS = 172800

    def __init__(self, order_manager=None, strategies: list = None, regime_manager=None):
        self.api_key = os.getenv('APCA_API_KEY_ID') or os.getenv('ALPACA_API_KEY')
        self.secret_key = os.getenv('APCA_API_SECRET_KEY') or os.getenv('ALPACA_SECRET_KEY')

        # Detección automática (PK -> Paper, AK -> Live)
        self.paper = True if self.api_key and self.api_key.startswith('PK') else False

        try:
            self.client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.paper
            )
        except Exception as e:
            logger.critical(f"FALLO CRÍTICO DE CONEXIÓN ALPACA: {e}")
            self.client = None

        self.notifier = TelegramNotifier()
        self.order_manager = order_manager
        self.strategies = strategies or []
        self.regime_manager = regime_manager
        self._ath = 0.0
        self._halted = False
        self._halted_at: datetime | None = None
        self._48h_alert_sent = False

    def check(self) -> bool:
        """
        Verifica el estado del portfolio y la liquidez T+1.
        """
        global _PORTFOLIO_STATUS
        
        try:
            account = self.client.get_account()
            if not account: raise ValueError("Account data is None")

            equity = float(account.equity)
            # En Paper Trading no existe settled_cash (todo es instantáneo), en Live sí.
            settled_cash = float(getattr(account, 'settled_cash', account.cash if self.paper else 0.0))

            # Actualizar ATH
            if equity > self._ath:
                self._ath = equity

            # Calcular drawdown
            drawdown = (self._ath - equity) / self._ath if self._ath > 0 else 0.0

            # Actualizar estado global con settled_cash prioritario
            _PORTFOLIO_STATUS.update({
                "equity": round(equity, 2),
                "settled_cash": round(settled_cash, 2),
                "ath": round(self._ath, 2),
                "drawdown_pct": round(drawdown * 100, 2),
                "is_halted": self._halted,
                "last_check": datetime.now().isoformat(),
            })

            # Circuit Breaker por Drawdown
            if not self._halted and drawdown >= self.MAX_DRAWDOWN_PCT:
                self._trigger_halt(f"Drawdown {drawdown*100:.1f}% superó el límite.")
                return False

            # Auto-resume inteligente por régimen de mercado
            if self._halted and self._halted_at:
                elapsed = (datetime.now() - self._halted_at).total_seconds()

                # Alerta a las 48h si sigue halted
                if elapsed >= self.ALERT_HALT_SECS and not self._48h_alert_sent:
                    self.notifier.send_message(
                        "⚠️ <b>[CIRCUIT BREAKER]</b> 48h en halt. "
                        "El régimen sigue desfavorable. Revisión manual recomendada."
                    )
                    self._48h_alert_sent = True

                # No evaluar resume antes de 1h
                if elapsed >= self.MIN_HALT_SECS_BEFORE_RESUME and self.regime_manager:
                    try:
                        from engine.regime_manager import Regime
                        regime = self.regime_manager.assess_if_needed()
                        if regime in (Regime.BULL, Regime.CHOP):
                            logger.info(f"[PortfolioManager] Auto-resume: régimen {regime.value} favorable tras {elapsed/3600:.1f}h de halt.")
                            self.notifier.send_message(
                                f"🟢 <b>[CIRCUIT BREAKER]</b> Auto-resume activado.\n"
                                f"Régimen: {regime.value} | Tiempo en halt: {elapsed/3600:.1f}h"
                            )
                            self._48h_alert_sent = False
                            self.resume()
                            return True
                        else:
                            logger.info(f"[PortfolioManager] Halt activo. Régimen {regime.value} — esperando BULL/CHOP para reanudar.")
                    except Exception as re:
                        logger.warning(f"[PortfolioManager] Error evaluando régimen para resume: {re}")

            return True

        except Exception as e:
            logger.critical(f"[RISK ALERT] Fallo en API de Alpaca. Bloqueando operativa por seguridad: {e}")
            self._halted = True
            _PORTFOLIO_STATUS["is_halted"] = True
            _PORTFOLIO_STATUS["settled_cash"] = 0.0
            return False

    def can_afford(self, notional_amount: float) -> bool:
        """
        🛡️ VALIDACIÓN ANTI-GFV:
        Retorna True solo si hay suficiente settled_cash para cubrir el costo.
        Usa bloqueo local (spent_today) porque settled_cash no se deduce intradiariamente.
        """
        try:
            if self._halted: return False
            account = self.client.get_account()
            # Fallback para Paper Trading
            settled_cash = float(getattr(account, 'settled_cash', account.cash if self.paper else 0.0))
            
            # Inicializar spent_today si no existe
            if not hasattr(self, 'spent_today'):
                self.spent_today = 0.0

            available_now = settled_cash - self.spent_today
            
            if available_now >= notional_amount:
                self.spent_today += notional_amount
                logger.info(f"[PortfolioManager] Fondos validados: ${notional_amount} (Disponible ${available_now}, Spent ${self.spent_today})")
                return True
            else:
                logger.warning(f"[PortfolioManager] BLOQUEADO: No hay fondos asentados (T+1) para ${notional_amount} (Disponible: ${available_now})")
                return False
        except Exception as e:
            logger.critical(f"[PortfolioManager] Error en validación de fondos: {e}. Asumiendo $0.")
            return False

    def _trigger_halt(self, reason: str):
        self._halted = True
        self._halted_at = datetime.now()
        _PORTFOLIO_STATUS["is_halted"] = True
        _PORTFOLIO_STATUS["halt_reason"] = reason
        logger.critical(f"[PortfolioManager] 🚨 CIRCUIT BREAKER ACTIVADO: {reason}")
        
        for strat in self.strategies:
            strat.pause()

        try:
            self.client.close_all_positions(cancel_orders=True)
            logger.warning("[PortfolioManager] Liquidación de emergencia completada.")
        except:
             pass

        self.notifier.send_message(f"🚨 <b>[CIRCUIT BREAKER]</b>\n{reason}\nBot pausado.")

    def resume(self):
        self._halted = False
        self._halted_at = None
        _PORTFOLIO_STATUS["is_halted"] = False
        _PORTFOLIO_STATUS["halt_reason"] = None
        for strat in self.strategies:
            if hasattr(strat, 'resume'): strat.resume()
        logger.info("[PortfolioManager] Engine reactivado.")

    def is_halted(self) -> bool:
        return self._halted
