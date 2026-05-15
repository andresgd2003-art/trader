"""
strategies/strat_05_rsi_dip.py — RSI Buy the Dip (Regime-Aware)

Cambio 2026-05-15: Multi-símbolo según régimen.
  BULL → TQQQ (3x Nasdaq, dip-buy en rally)
  BEAR → GLD  (flight-to-safety, dip-buy en oro)

Cada símbolo tiene tracking independiente de RSI, posición y entry price.
Las salidas SL/TP se evalúan SIEMPRE sobre cualquier símbolo abierto
(aunque el régimen cambie, las posiciones existentes se protegen).
Las nuevas compras solo se permiten en el símbolo target del régimen actual.
"""
import logging
import pandas as pd
from collections import deque
from ta.momentum import RSIIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIDipStrategy(BaseStrategy):

    STRAT_NUMBER = 5
    SYMBOL_BY_REGIME = {
        "BULL": "TQQQ",
        "BEAR": "GLD",
    }
    SYMBOLS = ["TQQQ", "GLD"]

    RSI_PERIOD = 14
    RSI_BUY    = 45
    RSI_SELL   = 65
    STOP_LOSS_PCT   = 0.025
    TAKE_PROFIT_PCT = 0.05
    MIN_BARS_BETWEEN_BUYS = 5

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="RSI Buy the Dip",
            symbols=list(self.SYMBOLS),
            order_manager=order_manager
        )
        self.regime_manager = regime_manager

        # Estado por símbolo
        self._closes = {s: deque(maxlen=50) for s in self.SYMBOLS}
        self._has_position = {s: False for s in self.SYMBOLS}
        self._entry_price = {s: 0.0 for s in self.SYMBOLS}
        self._last_buy_bar = {s: -self.MIN_BARS_BETWEEN_BUYS for s in self.SYMBOLS}
        self._bar_count = 0

        # Sync posiciones existentes desde Alpaca
        for sym in self.SYMBOLS:
            qty = self.sync_position_from_alpaca(sym)
            if qty > 0:
                self._has_position[sym] = True
                try:
                    pos = self.order_manager.client.get_open_position(sym)
                    self._entry_price[sym] = float(pos.avg_entry_price)
                    logger.info(f"[{self.name}] Entry {sym} sincronizado: ${self._entry_price[sym]:.2f}")
                except Exception:
                    pass

    def _target_symbol(self):
        """Símbolo que la estrategia debe comprar en el régimen actual (None si no aplica)."""
        if not self.regime_manager:
            return None
        from engine.regime_manager import get_current_regime
        regime = get_current_regime().get("regime", "UNKNOWN")
        return self.SYMBOL_BY_REGIME.get(regime)

    async def on_bar(self, bar) -> None:
        sym = bar.symbol
        if sym not in self.SYMBOLS:
            return

        self._bar_count += 1
        self._closes[sym].append(float(bar.close))

        if len(self._closes[sym]) < self.RSI_PERIOD + 1:
            return

        s = pd.Series(list(self._closes[sym]))
        current_rsi = RSIIndicator(close=s, window=self.RSI_PERIOD).rsi().iloc[-1]
        if pd.isna(current_rsi):
            return

        logger.info(f"[{self.name}] {sym} RSI={current_rsi:.1f} Precio={bar.close:.2f}")

        # Salidas SIEMPRE — protegen capital aunque el régimen cambie
        if self._has_position[sym] and self._entry_price[sym] > 0:
            ret = (float(bar.close) / self._entry_price[sym]) - 1.0
            if ret <= -self.STOP_LOSS_PCT:
                logger.info(f"[{self.name}] 🛑 SL {ret*100:+.2f}% → VENDIENDO {sym}")
                await self.order_manager.sell(sym, strategy_name=self.name)
                self._has_position[sym] = False
                self._position[sym] = 0
                self._entry_price[sym] = 0.0
                return
            if ret >= self.TAKE_PROFIT_PCT:
                logger.info(f"[{self.name}] 💰 TP {ret*100:+.2f}% → VENDIENDO {sym}")
                await self.order_manager.sell(sym, strategy_name=self.name)
                self._has_position[sym] = False
                self._position[sym] = 0
                self._entry_price[sym] = 0.0
                return

        # Salida por RSI alto (mantiene la lógica original)
        if current_rsi > self.RSI_SELL and self._has_position[sym]:
            logger.info(f"[{self.name}] 🔴 RSI={current_rsi:.1f} > {self.RSI_SELL} → VENDIENDO {sym}")
            queued = await self.order_manager.sell(sym, strategy_name=self.name)
            if queued:
                self._has_position[sym] = False
                self._position[sym] = 0
                self._entry_price[sym] = 0.0
            return

        # Compras solo sobre el símbolo target del régimen actual
        target = self._target_symbol()
        if sym != target:
            return

        if current_rsi < self.RSI_BUY and not self._has_position[sym]:
            bars_since = self._bar_count - self._last_buy_bar[sym]
            if bars_since < self.MIN_BARS_BETWEEN_BUYS:
                return

            real_qty = self.sync_position_from_alpaca(sym)
            if real_qty > 0:
                self._has_position[sym] = True
                return

            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"):
                return

            logger.info(f"[{self.name}] 🟢 RSI={current_rsi:.1f} < {self.RSI_BUY} → COMPRANDO {sym}")
            queued = await self.order_manager.buy(sym, strategy_name=self.name)
            if queued:
                self._has_position[sym] = True
                self._position[sym] = 1
                self._entry_price[sym] = float(bar.close)
                self._last_buy_bar[sym] = self._bar_count
