"""
engine/base_strategy.py
=======================
Clase base abstracta que TODAS las estrategias deben heredar.
Define el "contrato" que garantiza que el dispatcher pueda
comunicarse con cualquier estrategia de forma uniforme.
"""
from abc import ABC, abstractmethod
import logging
import os
from collections import deque

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    Clase abstracta base para todas las estrategias de trading.

    Cada estrategia concreta (ej: GoldenCrossStrategy) debe:
    1. Heredar de esta clase
    2. Implementar los métodos on_bar() y on_quote()
    3. Usar self.order_manager para enviar órdenes
    """

    def __init__(self, name: str, symbols: list[str], order_manager):
        self.name = name
        self.symbols = symbols          # Lista de activos que monitorea
        self.order_manager = order_manager
        self.is_active = True           # Se puede pausar desde el dashboard
        self._position = {}             # {symbol: quantity} posiciones abiertas
        self._history = deque(maxlen=500) # Prevención OOM
        
        logger.info(f"[{self.name}] Estrategia inicializada. Activos: {symbols}")

    @abstractmethod
    async def on_bar(self, bar) -> None:
        """
        Se llama cada vez que llega una vela (candle/bar) de mercado.
        Una 'bar' contiene: symbol, open, high, low, close, volume, timestamp.
        """
        pass

    async def on_quote(self, quote) -> None:
        """
        Se llama cada vez que llega una cotización bid/ask.
        Opcional: las estrategias pueden no implementarlo.
        """
        pass

    def should_process(self, symbol: str) -> bool:
        """Verifica si esta estrategia debe procesar el símbolo recibido."""
        return self.is_active and symbol in self.symbols

    def pause(self):
        """Pausa la estrategia (no enviará nuevas órdenes)."""
        self.is_active = False
        logger.warning(f"[{self.name}] Estrategia PAUSADA.")

    def resume(self):
        """Reactiva la estrategia."""
        self.is_active = True
        logger.info(f"[{self.name}] Estrategia REACTIVADA.")

    def get_status(self) -> dict:
        """Retorna el estado actual para el dashboard."""
        return {
            "name": self.name,
            "symbols": self.symbols,
            "is_active": self.is_active,
            "positions": self._position,
        }

    # ------------------------------------------------------------------ #
    #  UTILIDADES ANTI-DUPLICADO EN REINICIOS                             #
    # ------------------------------------------------------------------ #

    def sync_position_from_alpaca(self, symbol: str) -> float:
        """
        Consulta Alpaca para saber si ya existe una posición real en `symbol`.
        Retorna la cantidad (qty) si existe, 0.0 si no.
        Úsalo en __init__ de estrategias con _has_position para restaurar
        el estado tras reinicios sin duplicar órdenes.

        Ejemplo de uso:
            qty = self.sync_position_from_alpaca("BTC/USD")
            self._has_position = qty > 0
            self._current_qty = qty
        """
        try:
            from alpaca.trading.client import TradingClient
            api_key = os.environ.get("ALPACA_API_KEY", "")
            secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
            if not api_key:
                return 0.0
            client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
            # Alpaca usa BTCUSD sin slash para positions
            alpaca_sym = symbol.replace("/", "")
            try:
                pos = client.get_open_position(alpaca_sym)
                qty = float(pos.qty)
                logger.info(f"[{self.name}] 🔄 Posición sincronizada desde Alpaca: {symbol} qty={qty:.5f}")
                return qty
            except Exception:
                return 0.0
        except Exception as e:
            logger.warning(f"[{self.name}] sync_position_from_alpaca error: {e}")
            return 0.0

    def check_open_orders_exist(self, symbol: str) -> bool:
        """
        Verifica si ya existen órdenes abiertas (GTC/open) para `symbol` en Alpaca.
        Úsalo en grids para evitar redesplegar si ya hay órdenes activas.
        """
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import GetOrdersRequest
            api_key = os.environ.get("ALPACA_API_KEY", "")
            secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
            if not api_key:
                return False
            client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
            alpaca_sym = symbol.replace("/", "")
            orders = client.get_orders(GetOrdersRequest(status="open", symbols=[alpaca_sym]))
            has_orders = len(orders) > 0
            if has_orders:
                logger.info(f"[{self.name}] 🔄 {len(orders)} órdenes abiertas encontradas en Alpaca para {symbol}. Grid ya activa.")
            return has_orders
        except Exception as e:
            logger.warning(f"[{self.name}] check_open_orders_exist error: {e}")
            return False
