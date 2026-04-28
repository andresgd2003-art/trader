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

    STRAT_NUMBER = 4
    SYMBOL     = "DIA"
    FAST_EMA   = 12
    SLOW_EMA   = 26
    SIGNAL_EMA = 9
    FORCED_STOP_LOSS_PCT = 0.015   # -1.5%
    FORCED_TAKE_PROFIT_PCT = 0.02  # +2%

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="MACD Trend",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=100)
        self._prev_macd_above_signal = None
        self._entry_price = 0.0
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0
        if self._has_position:
            try:
                pos = self.order_manager.client.get_open_position(self.SYMBOL)
                self._entry_price = float(pos.avg_entry_price)
            except Exception:
                pass

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

        current_price = float(bar.close)

        # Salidas forzosas SL/TP — siempre evaluadas
        if self._has_position and self._entry_price > 0:
            ret = (current_price / self._entry_price) - 1.0
            if ret <= -self.FORCED_STOP_LOSS_PCT:
                logger.info(f"[{self.name}] 🛑 SL FORZOSO {ret*100:+.2f}% → VENDIENDO {self.SYMBOL}")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._entry_price = 0.0
                self._position[self.SYMBOL] = 0
                return
            if ret >= self.FORCED_TAKE_PROFIT_PCT:
                logger.info(f"[{self.name}] 💰 TP FORZOSO {ret*100:+.2f}% → VENDIENDO {self.SYMBOL}")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._entry_price = 0.0
                self._position[self.SYMBOL] = 0
                return

        if self._prev_macd_above_signal is not None:
            # Compra: MACD cruza por ARRIBA de la señal
            if macd_above and not self._prev_macd_above_signal:
                if not self._has_position:
                    logger.info(f"[{self.name}] 🟢 MACD cruzó arriba de señal. COMPRANDO {self.SYMBOL}")
                    if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"): return
                    await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
                    self._has_position = True
                    self._entry_price = current_price
                    self._position[self.SYMBOL] = 1

            # Venta: MACD cruza por ABAJO de la señal
            elif not macd_above and self._prev_macd_above_signal:
                if self._has_position:
                    logger.info(f"[{self.name}] 🔴 MACD cruzó abajo de señal. VENDIENDO {self.SYMBOL}")
                    await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                    self._has_position = False
                    self._entry_price = 0.0
                    self._position[self.SYMBOL] = 0

        self._prev_macd_above_signal = macd_above
