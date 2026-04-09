"""
strategies/strat_06_bollinger.py — Bollinger Band Reversion
============================================================
LÓGICA:
- Bandas Bollinger: SMA(20) ± 2 desviaciones estándar
- COMPRA cuando el precio toca la banda INFERIOR (precio muy bajo → rebote)
- VENDE cuando el precio vuelve a la SMA media (objetivo de ganancias moderado)

¿Por qué funciona?
Los precios tienden a revertir hacia su media. Cuando el precio
cae 2 desviaciones estándar por debajo del promedio (la banda inferior),
estadísticamente suele rebotar. Esta es una estrategia de "mean reversion".
"""
import logging
import pandas_ta as ta
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BollingerReversionStrategy(BaseStrategy):

    SYMBOL     = "SRVR"
    PERIOD     = 20
    STD_DEV    = 2.0
    QTY        = 10

    def __init__(self, order_manager):
        super().__init__(
            name="Bollinger Reversion",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._closes = deque(maxlen=50)
        self._has_position = False
        self._entry_sma = None  # SMA al momento de comprar (objetivo de salida)

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))

        if len(self._closes) < self.PERIOD:
            return

        closes = np.array(self._closes, dtype=float)
        import pandas as pd
        s = pd.Series(closes)
        bb = ta.bbands(s, length=self.PERIOD, std=self.STD_DEV)
        if bb is None or bb.empty:
            return

        curr_upper  = float(bb.iloc[-1, 0])  # BBU
        curr_middle = float(bb.iloc[-1, 1])  # BBM
        curr_lower  = float(bb.iloc[-1, 2])  # BBL
        curr_price  = float(bar.close)

        if np.isnan(curr_lower):
            return

        logger.info(
            f"[{self.name}] {bar.symbol} Precio={curr_price:.2f} "
            f"BB[{curr_lower:.2f} | {curr_middle:.2f} | {curr_upper:.2f}]"
        )

        if curr_price <= curr_lower and not self._has_position:
            logger.info(f"[{self.name}] 🟢 Precio tocó banda INFERIOR. COMPRANDO {self.SYMBOL}")
            await self.order_manager.buy(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
            self._has_position = True
            self._entry_sma    = curr_middle
            self._position[self.SYMBOL] = self.QTY

        elif self._has_position and curr_price >= curr_middle:
            logger.info(f"[{self.name}] 🔴 Precio llegó a la media. VENDIENDO {self.SYMBOL}")
            await self.order_manager.sell(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
            self._has_position = False
            self._entry_sma    = None
            self._position[self.SYMBOL] = 0
