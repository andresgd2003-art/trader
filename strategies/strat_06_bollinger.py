"""
strategies/strat_06_bollinger.py — Bollinger Band Reversion
"""
import logging
import numpy as np
import pandas as pd
from collections import deque
from ta.volatility import BollingerBands
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BollingerReversionStrategy(BaseStrategy):

    SYMBOL  = "SRVR"
    PERIOD  = 20
    STD_DEV = 2.0
    QTY     = 10

    def __init__(self, order_manager):
        super().__init__(
            name="Bollinger Reversion",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._closes = deque(maxlen=50)
        self._has_position = False

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))

        if len(self._closes) < self.PERIOD:
            return

        s = pd.Series(list(self._closes))
        bb = BollingerBands(close=s, window=self.PERIOD, window_dev=self.STD_DEV)

        curr_price  = float(bar.close)
        curr_upper  = bb.bollinger_hband().iloc[-1]
        curr_middle = bb.bollinger_mavg().iloc[-1]
        curr_lower  = bb.bollinger_lband().iloc[-1]

        if pd.isna(curr_lower):
            return

        logger.info(
            f"[{self.name}] {bar.symbol} Precio={curr_price:.2f} "
            f"BB[{curr_lower:.2f} | {curr_middle:.2f} | {curr_upper:.2f}]"
        )

        if curr_price <= curr_lower and not self._has_position:
            logger.info(f"[{self.name}] 🟢 Precio tocó banda INFERIOR. COMPRANDO {self.SYMBOL}")
            await self.order_manager.buy(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = self.QTY

        elif self._has_position and curr_price >= curr_middle:
            logger.info(f"[{self.name}] 🔴 Precio llegó a la media. VENDIENDO {self.SYMBOL}")
            await self.order_manager.sell(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0
