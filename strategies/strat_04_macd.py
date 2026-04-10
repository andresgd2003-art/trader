"""
strategies/strat_04_macd.py — MACD Trend Following
====================================================
LÓGICA:
- MACD = EMA(12) - EMA(26), Signal = EMA(9) del MACD
- COMPRA cuando MACD cruza Signal desde abajo Y MACD < 0 (zona de momentum)
- VENDE cuando MACD cruza Signal desde arriba Y MACD > 0
"""
import logging
import numpy as np
import pandas as pd
from collections import deque
from ta.trend import MACD
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class MACDTrendStrategy(BaseStrategy):

    SYMBOL     = "DIA"
    FAST_EMA   = 12
    SLOW_EMA   = 26
    SIGNAL_EMA = 9

    def __init__(self, order_manager):
        super().__init__(
            name="MACD Trend",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._closes = deque(maxlen=100)
        self._prev_macd_above_signal = None
        self._has_position = False

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))

        if len(self._closes) < self.SLOW_EMA + self.SIGNAL_EMA:
            return

        s = pd.Series(list(self._closes))
        macd_indicator = MACD(
            close=s,
            window_fast=self.FAST_EMA,
            window_slow=self.SLOW_EMA,
            window_sign=self.SIGNAL_EMA
        )

        current_macd   = macd_indicator.macd().iloc[-1]
        current_signal = macd_indicator.macd_signal().iloc[-1]

        if pd.isna(current_macd) or pd.isna(current_signal):
            return

        macd_above = current_macd > current_signal
        logger.info(f"[{self.name}] MACD={current_macd:.4f} Signal={current_signal:.4f}")

        if self._prev_macd_above_signal is not None:
            if macd_above and not self._prev_macd_above_signal and current_macd < 0:
                if not self._has_position:
                    logger.info(f"[{self.name}] 🟢 MACD cruzó arriba (zona negativa). COMPRANDO {self.SYMBOL}")
                    await self.order_manager.buy(self.SYMBOL, qty=15, strategy_name=self.name)
                    self._has_position = True
                    self._position[self.SYMBOL] = 15

            elif not macd_above and self._prev_macd_above_signal and current_macd > 0:
                if self._has_position:
                    logger.info(f"[{self.name}] 🔴 MACD cruzó abajo (zona positiva). VENDIENDO {self.SYMBOL}")
                    await self.order_manager.sell(self.SYMBOL, qty=15, strategy_name=self.name)
                    self._has_position = False
                    self._position[self.SYMBOL] = 0

        self._prev_macd_above_signal = macd_above
