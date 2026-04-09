"""
strategies/strat_05_rsi_dip.py — RSI Buy the Dip
"""
import logging
import numpy as np
import pandas as pd
from collections import deque
from ta.momentum import RSIIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIDipStrategy(BaseStrategy):

    SYMBOL     = "TQQQ"
    RSI_PERIOD = 14
    RSI_BUY    = 30
    RSI_SELL   = 70

    def __init__(self, order_manager):
        super().__init__(
            name="RSI Buy the Dip",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._closes = deque(maxlen=50)
        self._has_position = False

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

        logger.info(f"[{self.name}] {bar.symbol} RSI={current_rsi:.1f} Precio={bar.close:.2f}")

        if current_rsi < self.RSI_BUY and not self._has_position:
            logger.info(f"[{self.name}] 🟢 RSI={current_rsi:.1f} < {self.RSI_BUY} → COMPRANDO {self.SYMBOL}")
            await self.order_manager.buy(self.SYMBOL, qty=5, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = 5

        elif current_rsi > self.RSI_SELL and self._has_position:
            logger.info(f"[{self.name}] 🔴 RSI={current_rsi:.1f} > {self.RSI_SELL} → VENDIENDO {self.SYMBOL}")
            await self.order_manager.sell(self.SYMBOL, qty=5, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0
