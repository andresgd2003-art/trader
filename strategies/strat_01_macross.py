"""
strategies/strat_01_macross.py — Golden Cross / Death Cross
============================================================
LÓGICA:
- Calcula SMA de 50 días y SMA de 200 días con barras diarias
- COMPRA cuando SMA50 cruza POR ARRIBA a SMA200 ("Golden Cross")
- VENDE cuando SMA50 cruza POR ABAJO a SMA200 ("Death Cross")

¿Por qué funciona?
El Golden Cross es una señal de que la tendencia de corto plazo
supera a la de largo plazo → momento alcista. Es uno de los
indicadores más conocidos en el mercado.
"""
import logging
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class GoldenCrossStrategy(BaseStrategy):

    SYMBOL = "SMH"
    SMA_FAST = 50   # días
    SMA_SLOW = 200  # días

    def __init__(self, order_manager):
        super().__init__(
            name="Golden Cross",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        # Almacenamos los últimos 200 cierres para calcular SMAs
        self._closes = deque(maxlen=self.SMA_SLOW + 1)
        self._prev_fast_above = None    # Estado anterior (¿SMA50 estaba arriba?)
        self._has_position = False

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        # Agregar el precio de cierre al histórico
        self._closes.append(float(bar.close))

        # Necesitamos mínimo 200 barras para calcular SMA200
        if len(self._closes) < self.SMA_SLOW:
            logger.info(f"[{self.name}] Acumulando datos: {len(self._closes)}/{self.SMA_SLOW} barras")
            return

        closes = np.array(self._closes)
        sma_fast = closes[-self.SMA_FAST:].mean()
        sma_slow = closes[-self.SMA_SLOW:].mean()

        fast_above = sma_fast > sma_slow

        logger.info(f"[{self.name}] {bar.symbol} SMA{self.SMA_FAST}={sma_fast:.2f} SMA{self.SMA_SLOW}={sma_slow:.2f}")

        # Detectar cruce (el estado cambió respecto al anterior)
        if self._prev_fast_above is not None and fast_above != self._prev_fast_above:
            if fast_above and not self._has_position:
                # GOLDEN CROSS → Comprar
                logger.info(f"[{self.name}] 🟢 GOLDEN CROSS detectado en {bar.symbol}! Enviando orden de COMPRA.")
                await self.order_manager.buy(self.SYMBOL, qty=10, strategy_name=self.name)
                self._has_position = True
                self._position[self.SYMBOL] = 10

            elif not fast_above and self._has_position:
                # DEATH CROSS → Vender
                logger.info(f"[{self.name}] 🔴 DEATH CROSS detectado en {bar.symbol}! Enviando orden de VENTA.")
                await self.order_manager.sell(self.SYMBOL, qty=10, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0

        self._prev_fast_above = fast_above
