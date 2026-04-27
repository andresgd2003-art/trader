"""
strategies_crypto/strat_12_mean_reversion_extreme.py

12 - Extreme Mean Reversion (Crypto)
Assets: BTC/USD, ETH/USD
Lógica:
  Entry LONG:
    - close < BollingerBand Lower(20, 2.5)
    - RSI(14) < 25
  Exit:
    - close >= BB middle (SMA20) → reversión a la media
    - OR RSI > 50                → momentum recuperado
    - OR stop -3% desde entrada
"""
import logging
from collections import deque
import pandas as pd
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoMeanReversionExtreme(BaseStrategy):
    STRAT_NUMBER = 12
    SYMBOLS = ["BTC/USD", "ETH/USD"]
    BB_PERIOD = 20
    BB_STD = 2.5
    RSI_PERIOD = 14
    RSI_OVERSOLD = 35.0  # Bajado de 25 → 35 para más señales (menos selectivo)
    RSI_EXIT = 50.0
    STOP_LOSS_PCT = 0.03
    NOTIONAL_RISK_USD = 500.0
    HEARTBEAT_INTERVAL = 10

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Crypto Mean Reversion Extreme",
            symbols=list(self.SYMBOLS),
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = {sym: deque(maxlen=self.BB_PERIOD * 3) for sym in self.SYMBOLS}
        self._has_position = {}
        self._current_qty = {}
        self._entry_price = {}
        self._bar_count = {sym: 0 for sym in self.SYMBOLS}

        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        for sym in self.SYMBOLS:
            qty = self.sync_position_from_alpaca(sym)
            self._has_position[sym] = qty > 0
            self._current_qty[sym] = qty
            self._entry_price[sym] = 0.0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        symbol = bar.symbol
        if symbol not in self._closes:
            return

        current_close = float(bar.close)
        self._closes[symbol].append(current_close)
        self._bar_count[symbol] += 1

        min_bars = max(self.BB_PERIOD, self.RSI_PERIOD) + 1
        if len(self._closes[symbol]) < min_bars:
            return

        closes = pd.Series(list(self._closes[symbol]))
        bb = BollingerBands(closes, window=self.BB_PERIOD, window_dev=self.BB_STD)
        bb_lower = float(bb.bollinger_lband().iloc[-1])
        bb_middle = float(bb.bollinger_mavg().iloc[-1])
        rsi = float(RSIIndicator(closes, window=self.RSI_PERIOD).rsi().iloc[-1])

        # Heartbeat cada ~10 barras
        if self._bar_count[symbol] % self.HEARTBEAT_INTERVAL == 0:
            logger.info(
                f"[CryptoMeanReversionExtreme] {symbol} close={current_close:.2f} "
                f"BB_lower={bb_lower:.2f} RSI={rsi:.2f}"
            )

        has_pos = self._has_position.get(symbol, False)

        if not has_pos:
            # Entry LONG extrema
            if current_close < bb_lower and rsi < self.RSI_OVERSOLD:
                # Soft guard régimen
                if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"):
                    return
                granted = await self.order_manager.request_buy(
                    symbol=symbol, priority=3, strategy_name=self.name
                )
                if not granted:
                    logger.debug(f"[{self.name}] Árbitro denegó compra en {symbol}.")
                    return

                logger.info(
                    f"[{self.name}] 🟢 EXTREME MEAN REV {symbol}! "
                    f"close={current_close:.2f} < bb_low={bb_lower:.2f} RSI={rsi:.2f}"
                )
                if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"):
                    return
                await self.order_manager.buy(
                    symbol=symbol,
                    notional_usd=self.NOTIONAL_RISK_USD,
                    current_price=current_close,
                    strategy_name=self.name
                )
                qty_calc = self.order_manager._calculate_crypto_qty(self.NOTIONAL_RISK_USD, current_close)
                self._has_position[symbol] = True
                self._current_qty[symbol] = qty_calc
                self._entry_price[symbol] = current_close
                self._position[symbol] = qty_calc
        else:
            # Exit conditions
            entry = self._entry_price.get(symbol, 0.0)
            stop_price = entry * (1.0 - self.STOP_LOSS_PCT) if entry > 0 else 0.0

            exit_reason = None
            if current_close >= bb_middle:
                exit_reason = "Reversión a la media (SMA20)"
            elif rsi > self.RSI_EXIT:
                exit_reason = f"Momentum recuperado (RSI={rsi:.2f})"
            elif entry > 0 and current_close <= stop_price:
                exit_reason = f"Stop Loss -{self.STOP_LOSS_PCT*100:.1f}%"

            if exit_reason and self._current_qty.get(symbol, 0.0) > 0:
                logger.info(f"[{self.name}] 🔴 EXIT {symbol} — {exit_reason}. close={current_close:.2f}")
                await self.order_manager.sell_exact(
                    symbol=symbol,
                    exact_qty=self._current_qty[symbol],
                    strategy_name=self.name
                )
                self.order_manager.release_asset(symbol, self.name)
                self._has_position[symbol] = False
                self._current_qty[symbol] = 0.0
                self._entry_price[symbol] = 0.0
                self._position[symbol] = 0
