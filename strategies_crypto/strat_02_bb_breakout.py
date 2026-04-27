import logging
import numpy as np
from collections import deque
from engine.base_strategy import BaseStrategy
import pandas as pd
from ta.volatility import BollingerBands

logger = logging.getLogger(__name__)

class CryptoBBBreakoutStrategy(BaseStrategy):

    STRAT_NUMBER = 2
    SYMBOL = "ETH/USD"
    BB_PERIOD = 20
    BB_STD = 2.0
    SQUEEZE_PERIOD = 50
    NOTIONAL_RISK_USD = 1000.0
    FORCED_STOP_LOSS_PCT   = 0.04   # -4%
    FORCED_TAKE_PROFIT_PCT = 0.06   # +6%

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Bollinger Volatility Breakout",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=self.SQUEEZE_PERIOD + 20)
        self._volumes = deque(maxlen=self.BB_PERIOD)
        self._bandwidths = deque(maxlen=self.SQUEEZE_PERIOD)
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0
        self._current_qty = qty
        self._peak_price = 0.0
        self._entry_price = 0.0
        # Recuperar avg_entry_price real desde Alpaca tras restart
        if self._has_position:
            try:
                pos = self.order_manager.broker.get_position(self.SYMBOL) if hasattr(self.order_manager, "broker") else None
                if pos:
                    self._entry_price = float(pos.avg_entry_price)
                    self._peak_price = self._entry_price
            except Exception:
                pass

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return


        current_close = float(bar.close)
        current_volume = float(bar.volume)

        self._closes.append(current_close)
        self._volumes.append(current_volume)

        # === FORCED EXIT (SL -4% / TP +6%) por entry_price tracking ===
        if self._has_position and self._entry_price > 0 and self._current_qty > 0:
            pnl_pct = (current_close - self._entry_price) / self._entry_price
            if pnl_pct <= -self.FORCED_STOP_LOSS_PCT or pnl_pct >= self.FORCED_TAKE_PROFIT_PCT:
                tag = "SL -4%" if pnl_pct <= -self.FORCED_STOP_LOSS_PCT else "TP +6%"
                logger.warning(f"[{self.name}] FORCED {tag} en {self.SYMBOL}. entry={self._entry_price:.2f} now={current_close:.2f}")
                await self._exit_position()
                self._entry_price = 0.0
                return

        if len(self._closes) < self.BB_PERIOD:
            return

        closes = pd.Series(list(self._closes))
        volumes = np.array(self._volumes)

        bb = BollingerBands(closes, window=self.BB_PERIOD, window_dev=self.BB_STD)
        
        up = bb.bollinger_hband().iloc[-1]
        mid = bb.bollinger_mavg().iloc[-1]
        dn = bb.bollinger_lband().iloc[-1]
        
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
                # Consultar árbitro antes de comprar
                if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
                granted = await self.order_manager.request_buy(
                    symbol=self.SYMBOL, priority=4, strategy_name=self.name
                )
                if not granted:
                    logger.debug(f"[{self.name}] Árbitro denegó compra en {self.SYMBOL}.")
                    return

                logger.info(f"[{self.name}] 🚀 BB BREAKOUT & SQUEEZE en ETH! Comprando.")
                if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
                await self.order_manager.buy(
                    symbol=self.SYMBOL, 
                    notional_usd=self.NOTIONAL_RISK_USD, 
                    current_price=current_close,
                    strategy_name=self.name
                )
                self._has_position = True
                self._current_qty = self.order_manager._calculate_crypto_qty(self.NOTIONAL_RISK_USD, current_close)
                self._peak_price = current_close
                self._entry_price = current_close
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
        # Liberar el símbolo en el árbitro
        self.order_manager.release_asset(self.SYMBOL, self.name)
        self._has_position = False
        self._current_qty = 0.0
        self._peak_price = 0.0
