"""
strategies/strat_04_macd.py — MACD Trend Following
====================================================
LÓGICA:
- MACD = EMA(12) - EMA(26), Signal = EMA(9) del MACD
- COMPRA cuando MACD cruza Signal desde abajo Y MACD < 0 (zona de momentum)
- VENDE cuando MACD cruza Signal desde arriba Y MACD > 0

¿Por qué funciona?
El MACD mide la diferencia entre dos tendencias. Cruzar la línea de señal
en zona negativa captura el inicio de un movimiento alcista con momentum.
"""
import logging
import pandas_ta as ta
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class MACDTrendStrategy(BaseStrategy):

    SYMBOL     = "XLK"
    FAST_EMA   = 12
    SLOW_EMA   = 26
    SIGNAL_EMA = 9

    def __init__(self, order_manager):
        super().__init__(
            name="MACD Trend",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        # Necesitamos al menos 35 barras para calcular MACD estable
        self._closes = deque(maxlen=100)
        self._prev_macd_above_signal = None
        self._has_position = False

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))

        if len(self._closes) < self.SLOW_EMA + self.SIGNAL_EMA:
            return

        closes = np.array(self._closes, dtype=float)
        import pandas as pd
        s = pd.Series(closes)
        macd_df = ta.macd(s, fast=self.FAST_EMA, slow=self.SLOW_EMA, signal=self.SIGNAL_EMA)
        if macd_df is None or macd_df.empty:
            return

        macd_col   = [c for c in macd_df.columns if 'MACD_' in c and 'MACDs_' not in c and 'MACDh_' not in c]
        signal_col = [c for c in macd_df.columns if 'MACDs_' in c]
        if not macd_col or not signal_col:
            return

        current_macd   = float(macd_df[macd_col[0]].iloc[-1])
        current_signal = float(macd_df[signal_col[0]].iloc[-1])

        # Tomar los últimos valores válidos
        if np.isnan(current_macd) or np.isnan(current_signal):
            return

        macd_above     = current_macd > current_signal

        logger.info(f"[{self.name}] MACD={current_macd:.4f} Signal={current_signal:.4f}")

        if self._prev_macd_above_signal is not None:
            # Cruce al alza: MACD pasa de debajo a arriba de la señal
            if macd_above and not self._prev_macd_above_signal and current_macd < 0:
                if not self._has_position:
                    logger.info(f"[{self.name}] 🟢 MACD cruzó arriba (zona negativa). COMPRANDO {self.SYMBOL}")
                    await self.order_manager.buy(self.SYMBOL, qty=15, strategy_name=self.name)
                    self._has_position = True
                    self._position[self.SYMBOL] = 15

            # Cruce a la baja: MACD pasa de arriba a debajo de la señal
            elif not macd_above and self._prev_macd_above_signal and current_macd > 0:
                if self._has_position:
                    logger.info(f"[{self.name}] 🔴 MACD cruzó abajo (zona positiva). VENDIENDO {self.SYMBOL}")
                    await self.order_manager.sell(self.SYMBOL, qty=15, strategy_name=self.name)
                    self._has_position = False
                    self._position[self.SYMBOL] = 0

        self._prev_macd_above_signal = macd_above
