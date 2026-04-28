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

    STRAT_NUMBER = 1
    SYMBOL = "XLC"
    SMA_FAST = 50   # días
    SMA_SLOW = 200  # días
    FORCED_STOP_LOSS_PCT = 0.015   # -1.5%
    FORCED_TAKE_PROFIT_PCT = 0.025 # +2.5%

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Golden Cross",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=self.SMA_SLOW + 1)
        self._prev_fast_above = None
        self._entry_price = 0.0
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0
        if self._has_position:
            try:
                pos = self.order_manager.client.get_open_position(self.SYMBOL)
                self._entry_price = float(pos.avg_entry_price)
            except Exception:
                pass

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
        spread_pct = (sma_fast - sma_slow) / sma_slow * 100
        current_price = float(bar.close)

        logger.info(f"[{self.name}] {bar.symbol} SMA{self.SMA_FAST}={sma_fast:.2f} SMA{self.SMA_SLOW}={sma_slow:.2f} Spread={spread_pct:.2f}%")

        # Salidas forzosas SL/TP — siempre evaluadas
        if self._has_position and self._entry_price > 0:
            ret = (current_price / self._entry_price) - 1.0
            if ret <= -self.FORCED_STOP_LOSS_PCT:
                logger.info(f"[{self.name}] 🛑 SL FORZOSO {ret*100:+.2f}% → VENDIENDO {self.SYMBOL}")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._entry_price = 0.0
                self._position[self.SYMBOL] = 0
                return
            if ret >= self.FORCED_TAKE_PROFIT_PCT:
                logger.info(f"[{self.name}] 💰 TP FORZOSO {ret*100:+.2f}% → VENDIENDO {self.SYMBOL}")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._entry_price = 0.0
                self._position[self.SYMBOL] = 0
                return

        if self._prev_fast_above is not None and fast_above != self._prev_fast_above:
            if fast_above and not self._has_position:
                logger.info(f"[{self.name}] 🟢 GOLDEN CROSS detectado en {bar.symbol}! Enviando orden de COMPRA.")
                if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"): return
                await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
                self._has_position = True
                self._entry_price = current_price
                self._position[self.SYMBOL] = 1

            elif not fast_above and self._has_position:
                logger.info(f"[{self.name}] 🔴 DEATH CROSS detectado en {bar.symbol}! Enviando orden de VENTA.")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._entry_price = 0.0
                self._position[self.SYMBOL] = 0

        elif fast_above and spread_pct >= 0.2 and not self._has_position:
            logger.info(f"[{self.name}] 🟢 TENDENCIA ACTIVA: SMA50 > SMA200 (+{spread_pct:.2f}%). Entrando en {bar.symbol}.")
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"): return
            await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            self._has_position = True
            self._entry_price = current_price
            self._position[self.SYMBOL] = 1

        self._prev_fast_above = fast_above
