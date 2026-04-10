"""
strategies/strat_09_pairs.py — Pairs Trading (QQQ / XLK)
=========================================================
LÓGICA:
- Calcula el spread entre dos activos correlacionados: QQQ y XLK
  Spread = Precio(QQQ) / Precio(XLK)
- Calcula el Z-Score del spread en los últimos 20 días
- Si Z-Score > 2: QQQ está muy caro vs XLK → Short QQQ, Long XLK
- Si Z-Score < -2: QQQ está muy barato vs XLK → Long QQQ, Short XLK

¿Qué es el Z-Score?
Mide cuántas desviaciones estándar está el spread alejado de su media.
Z > 2 significa que la diferencia de precios es inusualmente alta
→ debería volver a la normalidad → oportunidad de arbitraje.

NOTA: Requiere cuenta de margen habilitada en Alpaca Paper Trading.
"""
import logging
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class PairsTradingStrategy(BaseStrategy):

    SYMBOL_A   = "QQQ"   # Activo A
    SYMBOL_B   = "XLK"   # Activo B
    LOOKBACK   = 20       # Días para calcular media y desviación del spread
    Z_ENTRY    = 2.0      # Z-Score para entrar
    Z_EXIT     = 0.5      # Z-Score para salir (vuelta a la media)
    QTY        = 10

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Pairs Trading",
            symbols=[self.SYMBOL_A, self.SYMBOL_B],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._price_a  = None
        self._price_b  = None
        self._spreads  = deque(maxlen=self.LOOKBACK)
        self._position_type = None   # "long_a_short_b" o "short_a_long_b"

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(9):
            return

        if bar.symbol == self.SYMBOL_A:
            self._price_a = float(bar.close)
        elif bar.symbol == self.SYMBOL_B:
            self._price_b = float(bar.close)

        # Necesitamos tener precio de ambos activos
        if self._price_a is None or self._price_b is None:
            return

        spread = self._price_a / self._price_b
        self._spreads.append(spread)

        if len(self._spreads) < self.LOOKBACK:
            return

        spreads_arr = np.array(self._spreads)
        mean   = spreads_arr.mean()
        std    = spreads_arr.std()

        if std == 0:
            return

        z_score = (spread - mean) / std

        logger.info(
            f"[{self.name}] {self.SYMBOL_A}/{self.SYMBOL_B} "
            f"Spread={spread:.4f} Z={z_score:.2f}"
        )

        if z_score > self.Z_ENTRY and self._position_type is None:
            # QQQ muy caro vs XLK → Short QQQ, Long XLK
            logger.info(f"[{self.name}] 📊 Z={z_score:.2f} > {self.Z_ENTRY} → Short {self.SYMBOL_A}, Long {self.SYMBOL_B}")
            await self.order_manager.sell(self.SYMBOL_A, qty=self.QTY, strategy_name=self.name)
            await self.order_manager.buy(self.SYMBOL_B, qty=self.QTY, strategy_name=self.name)
            self._position_type = "short_a_long_b"

        elif z_score < -self.Z_ENTRY and self._position_type is None:
            # QQQ muy barato vs XLK → Long QQQ, Short XLK
            logger.info(f"[{self.name}] 📊 Z={z_score:.2f} < -{self.Z_ENTRY} → Long {self.SYMBOL_A}, Short {self.SYMBOL_B}")
            await self.order_manager.buy(self.SYMBOL_A, qty=self.QTY, strategy_name=self.name)
            await self.order_manager.sell(self.SYMBOL_B, qty=self.QTY, strategy_name=self.name)
            self._position_type = "long_a_short_b"

        elif abs(z_score) < self.Z_EXIT and self._position_type is not None:
            # Spread volvió a la media → cerrar posición
            logger.info(f"[{self.name}] ↩️ Spread normalizado (Z={z_score:.2f}). Cerrando posición.")
            if self._position_type == "short_a_long_b":
                await self.order_manager.buy(self.SYMBOL_A, qty=self.QTY, strategy_name=self.name)
                await self.order_manager.sell(self.SYMBOL_B, qty=self.QTY, strategy_name=self.name)
            else:
                await self.order_manager.sell(self.SYMBOL_A, qty=self.QTY, strategy_name=self.name)
                await self.order_manager.buy(self.SYMBOL_B, qty=self.QTY, strategy_name=self.name)
            self._position_type = None
            self._position = {}
