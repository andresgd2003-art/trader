"""
strategies/strat_07_vix_filter.py — RSI en SPY
================================================
NOTA: El feed IEX gratuito no incluye VIXY.
Por ahora opera como RSI puro en SPY.
Cuando se tenga acceso al feed SIP (plan pagado),
se puede reactivar el filtro VIX.
"""
import logging
import pandas as pd
from collections import deque
from ta.momentum import RSIIndicator
import os
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class VIXFilteredReversionStrategy(BaseStrategy):

    SYMBOL      = "SPY"
    RSI_PERIOD  = 14
    RSI_BUY     = 30
    RSI_SELL    = 70

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="RSI + VIX Filter",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=50)
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))

        if len(self._closes) < self.RSI_PERIOD + 1:
            return

        s = pd.Series(list(self._closes))
        rsi_indicator = RSIIndicator(close=s, window=self.RSI_PERIOD)
        current_rsi = rsi_indicator.rsi().iloc[-1]

        if pd.isna(current_rsi):
            return

        logger.info(f"[{self.name}] {self.SYMBOL} RSI={current_rsi:.1f}")

        if current_rsi < self.RSI_BUY and not self._has_position:
            logger.info(f"[{self.name}] 🟢 RSI={current_rsi:.1f} < {self.RSI_BUY} → COMPRANDO {self.SYMBOL}")
            await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = 1

        elif current_rsi > self.RSI_SELL and self._has_position:
            logger.info(f"[{self.name}] 🔴 RSI={current_rsi:.1f} > {self.RSI_SELL} → VENDIENDO {self.SYMBOL}")
            await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0

