import logging
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy
import pandas as pd
from ta.trend import EMAIndicator

logger = logging.getLogger(__name__)

class CryptoEMACrossStrategy(BaseStrategy):

    SYMBOL = "BTC/USD"
    EMA_FAST = 12
    EMA_SLOW = 26
    NOTIONAL_RISK_USD = 1000.0  # Invertir 1000 usd por trade

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="EMA Trend Crossover",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=self.EMA_SLOW * 2) 
        self._prev_fast_above = None    
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0
        self._current_qty = qty

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        self._closes.append(float(bar.close))

        if len(self._closes) < self.EMA_SLOW * 2:
            return

        closes = pd.Series(list(self._closes))
        ema_fast = EMAIndicator(closes, window=self.EMA_FAST).ema_indicator().iloc[-1]
        ema_slow = EMAIndicator(closes, window=self.EMA_SLOW).ema_indicator().iloc[-1]

        fast_above = ema_fast > ema_slow

        logger.info(f"[{self.name}] {bar.symbol} EMA{self.EMA_FAST}={ema_fast:.2f} EMA{self.EMA_SLOW}={ema_slow:.2f}")

        if self._prev_fast_above is not None and fast_above != self._prev_fast_above:
            if fast_above and not self._has_position:
                # Consultar árbitro antes de comprar
                granted = await self.order_manager.request_buy(
                    symbol=self.SYMBOL, priority=4, strategy_name=self.name
                )
                if not granted:
                    logger.debug(f"[{self.name}] Árbitro denegó compra en {self.SYMBOL}.")
                    self._prev_fast_above = fast_above
                    return

                logger.info(f"[{self.name}] 🟢 EMA CROSSOVER en {bar.symbol}! Comprando ${self.NOTIONAL_RISK_USD}")
                await self.order_manager.buy(
                    symbol=self.SYMBOL, 
                    notional_usd=self.NOTIONAL_RISK_USD, 
                    current_price=float(bar.close),
                    strategy_name=self.name
                )
                self._has_position = True
                
                # Simularemos estado real. En produccion se puede pedir rest API en OrderManager.
                # Como es paper trading fraccionario, mantenemos conteo asumiendo fill exacto de límite:
                qty_calc = self.order_manager._calculate_crypto_qty(self.NOTIONAL_RISK_USD, float(bar.close))
                self._current_qty = qty_calc
                self._position[self.SYMBOL] = self._current_qty

            elif not fast_above and self._has_position:
                # Vender/Salir
                logger.info(f"[{self.name}] 🔴 EXIT EMA CROSS en {bar.symbol}! Vendiendo posición.")
                await self.order_manager.sell_exact(
                    symbol=self.SYMBOL, 
                    exact_qty=self._current_qty,
                    strategy_name=self.name
                )
                # Liberar el símbolo en el árbitro
                self.order_manager.release_asset(self.SYMBOL, self.name)
                self._has_position = False
                self._current_qty = 0.0
                self._position[self.SYMBOL] = 0

        self._prev_fast_above = fast_above
