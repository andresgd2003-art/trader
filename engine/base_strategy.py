"""
engine/base_strategy.py
=======================
Clase base abstracta que TODAS las estrategias deben heredar.
Define el "contrato" que garantiza que el dispatcher pueda
comunicarse con cualquier estrategia de forma uniforme.
"""
from abc import ABC, abstractmethod
import logging
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
