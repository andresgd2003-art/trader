"""
strategies/strat_05_rsi_dip.py — RSI Buy the Dip
==================================================
LÓGICA:
- Calcula RSI de 14 períodos en barras de 15 minutos
- COMPRA si RSI < 30 (el activo está "sobrevendido" → posible rebote)
- VENDE si RSI > 70 (el activo está "sobrecomprado" → posible caída)

¿Qué es RSI?
Relative Strength Index: un oscilador entre 0 y 100.
< 30 = el mercado vendió DEMASIADO → oportunidad de compra.
> 70 = el mercado compró DEMASIADO → momento de salir.
"""
import logging
import pandas_ta as ta
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIDipStrategy(BaseStrategy):

    SYMBOL     = "TQQQ"
    RSI_PERIOD = 14
    RSI_BUY    = 30     # Zona de sobreventa
    RSI_SELL   = 70     # Zona de sobrecompra

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

        closes = np.array(self._closes, dtype=float)
        import pandas as pd
        s = pd.Series(closes)
        rsi_series = ta.rsi(s, length=self.RSI_PERIOD)
        if rsi_series is None or rsi_series.empty:
            return
        current_rsi = float(rsi_series.iloc[-1])

        if np.isnan(current_rsi):
            return

        logger.info(f"[{self.name}] {bar.symbol} RSI={current_rsi:.1f} Precio={bar.close:.2f}")

        if current_rsi < self.RSI_BUY and not self._has_position:
            logger.info(f"[{self.name}] 🟢 RSI={current_rsi:.1f} < {self.RSI_BUY} → COMPRANDO {self.SYMBOL} (sobreventa)")
            await self.order_manager.buy(self.SYMBOL, qty=5, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = 5

        elif current_rsi > self.RSI_SELL and self._has_position:
            logger.info(f"[{self.name}] 🔴 RSI={current_rsi:.1f} > {self.RSI_SELL} → VENDIENDO {self.SYMBOL} (sobrecompra)")
            await self.order_manager.sell(self.SYMBOL, qty=5, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0
