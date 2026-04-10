import logging
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy
import talib

logger = logging.getLogger(__name__)

class CryptoBBBreakoutStrategy(BaseStrategy):

    SYMBOL = "ETH/USD"
    BB_PERIOD = 20
    BB_STD = 2.0
    SQUEEZE_PERIOD = 50
    NOTIONAL_RISK_USD = 1000.0

    def __init__(self, order_manager):
        super().__init__(
            name="Bollinger Volatility Breakout",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._closes = deque(maxlen=self.SQUEEZE_PERIOD + 20)
        self._volumes = deque(maxlen=self.BB_PERIOD)
        self._bandwidths = deque(maxlen=self.SQUEEZE_PERIOD)
        self._has_position = False
        self._current_qty = 0.0
        self._peak_price = 0.0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        current_close = float(bar.close)
        current_volume = float(bar.volume)

        self._closes.append(current_close)
        self._volumes.append(current_volume)

        if len(self._closes) < self.BB_PERIOD:
            return

        closes = np.array(self._closes)
        volumes = np.array(self._volumes)

        upper, middle, lower = talib.BBANDS(closes, timeperiod=self.BB_PERIOD, nbdevup=self.BB_STD, nbdevdn=self.BB_STD)
        
        up = upper[-1]
        mid = middle[-1]
        dn = lower[-1]
        
        bw = (up - dn) / mid if mid != 0 else 0
        self._bandwidths.append(bw)

        vol_sma = np.mean(volumes)

        # Logica de Squeeze (Bandwidth menor de últimos 50 periodos)
        if len(self._bandwidths) == self.SQUEEZE_PERIOD:
            min_bw = min(list(self._bandwidths)[:-1]) # Lowest except current
            is_squeeze = bw <= min_bw
        else:
            is_squeeze = False

        if not self._has_position:
            # Entry logic
            if is_squeeze and current_close > up and current_volume > (vol_sma * 1.5):
                logger.info(f"[{self.name}] 🚀 BB BREAKOUT & SQUEEZE en ETH! Vol:{current_volume:.2f} > SMA Vol. Comprando.")
                await self.order_manager.buy(
                    symbol=self.SYMBOL, 
                    notional_usd=self.NOTIONAL_RISK_USD, 
                    current_price=current_close,
                    strategy_name=self.name
                )
                self._has_position = True
                self._current_qty = self.order_manager._calculate_crypto_qty(self.NOTIONAL_RISK_USD, current_close)
                self._peak_price = current_close
        else:
            # Salida: Trailing stop 3% o debajo del SMA_20 middle band
            self._peak_price = max(self._peak_price, current_close)
            trailing_stop = self._peak_price * 0.97

            if current_close < trailing_stop:
                logger.info(f"[{self.name}] 🔴 TRAILING STOP (-3%) tocado en {self.SYMBOL}.")
                await self._exit_position()
            elif current_close < mid:
                logger.info(f"[{self.name}] 🔴 Cierre por debajo de SMA_20 en {self.SYMBOL}.")
                await self._exit_position()

    async def _exit_position(self):
        await self.order_manager.sell_exact(
            symbol=self.SYMBOL, 
            exact_qty=self._current_qty,
            strategy_name=self.name
        )
        self._has_position = False
        self._current_qty = 0.0
        self._peak_price = 0.0
