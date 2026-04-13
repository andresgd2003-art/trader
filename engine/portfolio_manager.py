"""
engine/portfolio_manager.py
============================
PortfolioManager — Gestor de riesgo global para el Equities Engine.

REGLAS inviolables (DIRECTIVE 4):
  1. Max position: $100 USD por trade (micro-sizing)
  2. Max drawdown: si el equity cae >10% del ATH → pausa total + liquida
  3. Trailing stop: recordatorio para las estrategias de alta volatilidad

Monitorea el portfolio en tiempo real y activa el "circuit breaker"
si el equity cae por debajo del umbral de drawdown.

Estado global accesible en el dashboard via get_portfolio_status().
"""

import os
import logging
from alpaca.trading.client import TradingClient
from engine.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

# Estado global para el dashboard
_PORTFOLIO_STATUS: dict = {
    "equity": 0.0,
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
    Gestor de riesgo del portfolio del Equities Engine.
    Se ejecuta periódicamente para verificar el estado de las cuentas.
    """

    MAX_DRAWDOWN_PCT = 0.10   # 10% caída desde ATH → halt
    MAX_POSITION_USD = 100.0  # Micro-sizing global

    def __init__(self, order_manager=None, strategies: list = None):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"

        self.client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper
        )
        self.notifier = TelegramNotifier()
        self.order_manager = order_manager
        self.strategies = strategies or []
        self._ath = 0.0
        self._halted = False

    def check(self) -> bool:
        """
        Verifica el estado del portfolio.
        Retorna True si todo está bien, False si activó el halt.
        """
        global _PORTFOLIO_STATUS
        from datetime import datetime

        try:
            account = self.client.get_account()
            equity = float(account.equity)

            # Actualizar ATH
            if equity > self._ath:
                self._ath = equity

            # Calcular drawdown
            drawdown = 0.0
            if self._ath > 0:
                drawdown = (self._ath - equity) / self._ath

            # Actualizar estado global
            _PORTFOLIO_STATUS.update({
                "equity": round(equity, 2),
                "ath": round(self._ath, 2),
                "drawdown_pct": round(drawdown * 100, 2),
                "is_halted": self._halted,
                "halt_reason": _PORTFOLIO_STATUS.get("halt_reason"),
                "last_check": datetime.now().isoformat(),
            })

            # ── Circuit Breaker ──
            if not self._halted and drawdown >= self.MAX_DRAWDOWN_PCT:
                self._trigger_halt(
                    f"Drawdown del {drawdown*100:.1f}% superó el límite del {self.MAX_DRAWDOWN_PCT*100:.0f}%"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"[PortfolioManager] Error verificando portfolio: {e}")
            return True  # No bloquear en caso de error de API

    def validate_gfv(self, order_cost: float) -> bool:
        """Verifica si hay enough settled_cash para evitar GFV."""
        try:
            account = self.client.get_account()
            settled_cash = float(getattr(account, 'settled_cash', 0.0))
            if order_cost > settled_cash:
                logger.warning(f"[PortfolioManager] GFV REJECT: Costo ${order_cost:.2f} > Settled ${settled_cash:.2f}")
                return False
            return True
        except Exception as e:
            logger.error(f"[PortfolioManager] Error validando GFV: {e}")
            return False

    def _trigger_halt(self, reason: str):
        """Activa el circuit breaker: pausa estrategias y liquida posiciones."""
        self._halted = True
        _PORTFOLIO_STATUS["is_halted"] = True
        _PORTFOLIO_STATUS["halt_reason"] = reason

        logger.critical(f"[PortfolioManager] 🚨 CIRCUIT BREAKER ACTIVADO: {reason}")

        # Pausar todas las estrategias de equities
        for strat in self.strategies:
            strat.pause()

        # Liquidar todas las posiciones abiertas
        try:
            self.client.close_all_positions(cancel_orders=True)
            logger.warning("[PortfolioManager] Todas las posiciones LIQUIDADAS.")
        except Exception as e:
            logger.error(f"[PortfolioManager] Error liquidando: {e}")

        # Notificar
        self.notifier.send_message(
            f"🚨 <b>[CIRCUIT BREAKER]</b>\n{reason}\n"
            f"Todas las posiciones de Equities liquidadas. Engine pausado."
        )

    def resume(self):
        """Reactiva el engine (uso manual)."""
        self._halted = False
        _PORTFOLIO_STATUS["is_halted"] = False
        _PORTFOLIO_STATUS["halt_reason"] = None
        for strat in self.strategies:
            strat.resume()
        logger.info("[PortfolioManager] Engine reactivado manualmente.")

    def is_halted(self) -> bool:
        return self._halted
