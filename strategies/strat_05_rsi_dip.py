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
    RSI_BUY    = 45   # Era 30 — sube para capturar correcciones moderadas en rally
    RSI_SELL   = 65   # Era 70 — baja para asegurar ganancia antes de sobrecompra

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="RSI Buy the Dip",
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

        logger.info(f"[{self.name}] {bar.symbol} RSI={current_rsi:.1f} Precio={bar.close:.2f}")

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
