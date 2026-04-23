"""
strategies/strat_02_donchian.py — Donchian Breakouts
=====================================================
LÓGICA:
- Calcula el máximo de los últimos 20 días (canal superior)
- Calcula el mínimo de los últimos 10 días (canal inferior)
- COMPRA cuando el precio actual supera el máximo de 20 días → breakout alcista
- VENDE cuando el precio cae bajo el mínimo de 10 días → salida de posición

¿Por qué funciona?
Si el precio rompe un máximo histórico reciente, sugiere un momentum
fuerte. Los canales Donchian capturan tendencias en ambas direcciones.
"""
import logging
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class DonchianBreakoutStrategy(BaseStrategy):

    STRAT_NUMBER = 2
    SYMBOL = "IWM"
    HIGH_PERIOD = 120  # 2 horas de barras de 1min — breakout intraday
    LOW_PERIOD  = 60   # 1 hora para el canal inferior

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Donchian Breakout",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._highs  = deque(maxlen=self.HIGH_PERIOD)
        self._lows   = deque(maxlen=self.HIGH_PERIOD)
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"):
            return

        self._highs.append(float(bar.high))
        self._lows.append(float(bar.low))

        if len(self._highs) < self.HIGH_PERIOD:
            return

        current_price = float(bar.close)
        channel_high  = max(self._highs)
        # Canal bajo: solo los últimos LOW_PERIOD valores
        channel_low   = min(list(self._lows)[-self.LOW_PERIOD:])

        logger.info(
            f"[{self.name}] {bar.symbol} Precio={current_price:.2f} "
            f"Canal: [{channel_low:.2f} - {channel_high:.2f}]"
        )

        if current_price >= channel_high and not self._has_position:
            logger.info(f"[{self.name}] 🟢 BREAKOUT ALCISTA! {bar.symbol} @ {current_price:.2f}")
            await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = 1

        elif current_price <= channel_low and self._has_position:
            logger.info(f"[{self.name}] 🔴 RUPTURA BAJISTA! Saliendo de {bar.symbol} @ {current_price:.2f}")
            await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0
