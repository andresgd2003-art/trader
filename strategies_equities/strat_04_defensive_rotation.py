"""
strategies_equities/strat_04_defensive_rotation.py — Defensive Rotation Strategy
"""
import logging
import pandas as pd
from collections import deque
import ta
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class DefensiveRotation(BaseStrategy):
    STRAT_NUMBER = 4
    SYMBOLS = ["KO", "PG", "JNJ", "WMT", "PEP", "SPY"] # SPY needed for market RSI

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="DefensiveRotation",
            symbols=self.SYMBOLS,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = {sym: deque(maxlen=50) for sym in self.symbols}
        
        # Track position
        self._has_position = {sym: False for sym in self.symbols if sym != "SPY"}
        self._qty_bought = {sym: 0.0 for sym in self.symbols if sym != "SPY"}
        self._entry_price = {sym: 0.0 for sym in self.symbols if sym != "SPY"}
        
        for sym in self._has_position.keys():
            qty = self.sync_position_from_alpaca(sym)
            self._has_position[sym] = qty > 0
            self._qty_bought[sym] = qty

    async def on_bar(self, bar) -> None:
        sym = bar.symbol
        if not self.should_process(sym):
            return

        # We need SPY for market RSI
        self._closes[sym].append(float(bar.close))
        
        if len(self._closes["SPY"]) < 15:
            return

        # Check Regime
        regime_str = "UNKNOWN"
        if self.regime_manager:
            regime_str = self.regime_manager.get_current_regime().get("regime", "UNKNOWN")
            
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="equities"):
            # If regime is BULL, we exit all defensive positions
            if regime_str == "BULL":
                for s in self._has_position.keys():
                    if self._has_position[s]:
                        logger.info(f"[{self.name}] 🔴 Régimen BULL detectado. Saliendo de posición defensiva {s}.")
                        await self._sell_position(s)
            return

        spy_s = pd.Series(list(self._closes["SPY"]))
        spy_rsi = ta.momentum.RSIIndicator(spy_s, window=14).rsi().iloc[-1]
        
        if sym == "SPY":
            # Just updating SPY data
            return
            
        # Exit Condition: RSI(SPY) > 55
        if self._has_position[sym] and spy_rsi > 55:
            logger.info(f"[{self.name}] 🔴 SPY RSI > 55 ({spy_rsi:.2f}). Saliendo de posición defensiva {sym}.")
            await self._sell_position(sym)
            return
            
        # Stop +2% Take Profit check would go here if we tracked entry price accurately or via Alpaca stream
        
        # Entry Condition
        if not self._has_position[sym] and spy_rsi < 40 and regime_str in ["BEAR", "CHOP"]:
            if len(self._closes[sym]) >= 15:
                sym_s = pd.Series(list(self._closes[sym]))
                sym_rsi = ta.momentum.RSIIndicator(sym_s, window=14).rsi().iloc[-1]
                
                logger.info(f"[{self.name}] 🟢 Régimen {regime_str}, SPY RSI {spy_rsi:.2f} < 40. {sym} RSI = {sym_rsi:.2f}. COMPRANDO {sym}")
                
                # Logic for "lowest RSI in universe" would typically require collecting all RSIs
                # on a timer or checking them all at once. For on_bar, we just buy if it triggers.
                # To be exact with "lowest RSI", we would need to coordinate across symbols.
                
                self._qty_bought[sym] = 1.0 # placeholder
                await self.order_manager.buy(sym, strategy_name=self.name)
                self._has_position[sym] = True

    async def _sell_position(self, sym):
        if hasattr(self.order_manager, 'sell_exact') and self._qty_bought[sym] > 0:
            await self.order_manager.sell_exact(sym, self._qty_bought[sym], strategy_name=self.name)
        else:
            await self.order_manager.sell(sym, strategy_name=self.name)
        self._has_position[sym] = False
        self._qty_bought[sym] = 0.0
