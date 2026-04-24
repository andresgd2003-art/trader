"""
strategies_crypto/strat_12_mean_reversion_extreme.py — Crypto Mean Reversion Extreme
"""
import logging
import pandas as pd
from collections import deque
import ta
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoMeanReversionExtreme(BaseStrategy):
    STRAT_NUMBER = 12
    SYMBOLS = ["BTC/USD", "ETH/USD"]

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="CryptoMeanReversionExtreme",
            symbols=self.SYMBOLS,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = {sym: deque(maxlen=50) for sym in self.symbols}
        
        # Track position
        self._has_position = {sym: False for sym in self.symbols}
        self._qty_bought = {sym: 0.0 for sym in self.symbols}
        self._entry_price = {sym: 0.0 for sym in self.symbols}
        
        for sym in self.symbols:
            qty = self.sync_position_from_alpaca(sym)
            self._has_position[sym] = qty > 0
            self._qty_bought[sym] = qty

    async def on_bar(self, bar) -> None:
        sym = bar.symbol
        if not self.should_process(sym):
            return

        # Regime gate: we want this to run in BEAR, CHOP, BULL, but specifically BEAR/CHOP

        self._closes[sym].append(float(bar.close))
        
        if len(self._closes[sym]) < 20:
            return

        s = pd.Series(list(self._closes[sym]))
        
        # RSI(14)
        rsi = ta.momentum.RSIIndicator(s, window=14).rsi().iloc[-1]
        
        # BB(20, 2.5)
        bb = ta.volatility.BollingerBands(close=s, window=20, window_dev=2.5)
        bb_lower = bb.bollinger_lband().iloc[-1]
        bb_middle = bb.bollinger_mavg().iloc[-1]
        
        curr_price = float(bar.close)

        # Exit Condition: close > BB_middle OR RSI > 50 OR stop -3%
        if self._has_position[sym]:
            stop_price = self._entry_price[sym] * 0.97 if self._entry_price[sym] > 0 else 0
            
            if curr_price >= bb_middle or rsi > 50 or (stop_price > 0 and curr_price <= stop_price):
                logger.info(f"[{self.name}] 🔴 Exit cond met for {sym} (Price: {curr_price:.2f}, RSI: {rsi:.2f}). VENDIENDO.")
                if hasattr(self.order_manager, 'sell_exact') and self._qty_bought[sym] > 0:
                    await self.order_manager.sell_exact(sym, self._qty_bought[sym], strategy_name=self.name)
                else:
                    await self.order_manager.sell(sym, strategy_name=self.name)
                self._has_position[sym] = False
                self._qty_bought[sym] = 0.0
                return

        # Entry Condition
        if not self._has_position[sym] and curr_price < bb_lower and rsi < 25:
            logger.info(f"[{self.name}] 🟢 Extreme oversold {sym} (Price: {curr_price:.2f} < BB_lower: {bb_lower:.2f}, RSI: {rsi:.2f} < 25). COMPRANDO.")
            
            self._entry_price[sym] = curr_price
            self._qty_bought[sym] = 1.0 # placeholder
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
            await self.order_manager.buy(sym, strategy_name=self.name)
            self._has_position[sym] = True
