"""
strategies/strat_07_vix_filter.py — RSI + Filtro VIX
======================================================
LÓGICA:
- Igual que la estrategia RSI (compra en sobreventa, vende en sobrecompra)
- PERO: si el VIX (índice de miedo) > 30, se bloquean TODAS las compras
- Cuando el mercado tiene mucho miedo (VIX alto), es más arriesgado comprar

¿Qué es el VIX?
El VIX mide la volatilidad implícita del S&P 500.
VIX > 30 = el mercado tiene MUCHO miedo → "Risk Off" (modo defensivo).
VIX < 20 = mercado tranquilo → normal trading.
"""
import logging
import pandas_ta as ta
import numpy as np
from collections import deque
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timezone, timedelta
import os
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class VIXFilteredReversionStrategy(BaseStrategy):

    SYMBOL      = "SPY"
    VIX_SYMBOL  = "VIXY"   # ETF proxy del VIX (VIX real no es tradeable directamente)
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
        """Obtiene el nivel actual de VIXY como proxy del VIX."""
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

        closes = np.array(self._closes, dtype=float)
        import pandas as pd
        s = pd.Series(closes)
        rsi_series = ta.rsi(s, length=self.RSI_PERIOD)
        if rsi_series is None or rsi_series.empty:
            return
        current_rsi = float(rsi_series.iloc[-1])

        if np.isnan(current_rsi):
            return

        risk_off = self._vix_level > self.VIX_LIMIT
        logger.info(
            f"[{self.name}] RSI={current_rsi:.1f} VIXY={self._vix_level:.1f} "
            f"{'🛑 RISK OFF' if risk_off else '✅ Risk On'}"
        )

        if current_rsi < self.RSI_BUY and not self._has_position:
            if risk_off:
                logger.warning(f"[{self.name}] Señal de COMPRA bloqueada: VIXY={self._vix_level:.1f} > {self.VIX_LIMIT}")
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
