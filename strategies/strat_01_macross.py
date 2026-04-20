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

    SYMBOL = "XLC"
    SMA_FAST = 50   # días
    SMA_SLOW = 200  # días

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Golden Cross",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        # Almacenamos los últimos 200 cierres para calcular SMAs
        self._closes = deque(maxlen=self.SMA_SLOW + 1)
        self._prev_fast_above = None    # Estado anterior (¿SMA50 estaba arriba?)
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0

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
        spread_pct = (sma_fast - sma_slow) / sma_slow * 100  # % de separación

        logger.info(f"[{self.name}] {bar.symbol} SMA{self.SMA_FAST}={sma_fast:.2f} SMA{self.SMA_SLOW}={sma_slow:.2f} Spread={spread_pct:.2f}%")

        # Modo 1: Detectar cruce (el estado cambió respecto al anterior)
        if self._prev_fast_above is not None and fast_above != self._prev_fast_above:
            if fast_above and not self._has_position:
                logger.info(f"[{self.name}] 🟢 GOLDEN CROSS detectado en {bar.symbol}! Enviando orden de COMPRA.")
                await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
                self._has_position = True
                self._position[self.SYMBOL] = 1

            elif not fast_above and self._has_position:
                logger.info(f"[{self.name}] 🔴 DEATH CROSS detectado en {bar.symbol}! Enviando orden de VENTA.")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0

        # Modo 2: Tendencia Activa — ya está en Golden Cross con spread > 0.2%
        # ⚠️ Seguridad: spread mínimo evita entradas cerca del cruce donde hay ruido
        elif fast_above and spread_pct >= 0.2 and not self._has_position:
            logger.info(f"[{self.name}] 🟢 TENDENCIA ACTIVA: SMA50 > SMA200 (+{spread_pct:.2f}%). Entrando en {bar.symbol}.")
            await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = 1

        self._prev_fast_above = fast_above
