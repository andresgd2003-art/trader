"""
strategies/strat_07_vix_filter.py — RSI + Filtro VIX
"""
import logging
import numpy as np
import pandas as pd
from collections import deque
from ta.momentum import RSIIndicator
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timezone, timedelta
import os
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class VIXFilteredReversionStrategy(BaseStrategy):

    SYMBOL      = "SPY"
    VIX_SYMBOL  = "VIXY"
    RSI_PERIOD  = 14
    RSI_BUY     = 30
    RSI_SELL    = 70
    VIX_LIMIT   = 30.0
    QTY         = 8

    def __init__(self, order_manager):
        super().__init__(
            name="RSI + VIX Filter",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._closes = deque(maxlen=50)
        self._has_position = False
        self._vix_level = 0.0
        self._data_client = StockHistoricalDataClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", "")
        )

    async def _update_vix(self):
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=5)
            request = StockBarsRequest(
                symbol_or_symbols=self.VIX_SYMBOL,
                timeframe=TimeFrame.Day,
                start=start, end=end
            )
            bars = self._data_client.get_stock_bars(request)
            df = bars.df
            if not df.empty:
                self._vix_level = float(df.iloc[-1]["close"])
        except Exception as e:
            logger.warning(f"[{self.name}] No se pudo obtener VIXY: {e}")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))
        await self._update_vix()

        if len(self._closes) < self.RSI_PERIOD + 1:
            return

        s = pd.Series(list(self._closes))
        rsi_indicator = RSIIndicator(close=s, window=self.RSI_PERIOD)
        current_rsi = rsi_indicator.rsi().iloc[-1]

        if pd.isna(current_rsi):
            return

        risk_off = self._vix_level > self.VIX_LIMIT
        logger.info(
            f"[{self.name}] RSI={current_rsi:.1f} VIXY={self._vix_level:.1f} "
            f"{'RISK OFF' if risk_off else 'Risk On'}"
        )

        if current_rsi < self.RSI_BUY and not self._has_position:
            if risk_off:
                logger.warning(f"[{self.name}] Señal bloqueada: VIXY={self._vix_level:.1f} > {self.VIX_LIMIT}")
            else:
                logger.info(f"[{self.name}] 🟢 RSI bajo + VIX ok → COMPRANDO {self.SYMBOL}")
                await self.order_manager.buy(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
                self._has_position = True
                self._position[self.SYMBOL] = self.QTY

        elif current_rsi > self.RSI_SELL and self._has_position:
            logger.info(f"[{self.name}] 🔴 RSI alto → VENDIENDO {self.SYMBOL}")
            await self.order_manager.sell(self.SYMBOL, qty=self.QTY, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0
