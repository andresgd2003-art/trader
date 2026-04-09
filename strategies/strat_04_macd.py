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
import talib
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
        macd, signal, hist = talib.MACD(
            closes,
            fastperiod=self.FAST_EMA,
            slowperiod=self.SLOW_EMA,
            signalperiod=self.SIGNAL_EMA
        )

        # Tomar los últimos valores válidos
        if np.isnan(macd[-1]) or np.isnan(signal[-1]):
            return

        current_macd   = macd[-1]
        current_signal = signal[-1]
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
