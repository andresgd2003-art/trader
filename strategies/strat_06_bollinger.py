"""
strategies/strat_06_bollinger.py — Bollinger Band Reversion
"""
import logging
import numpy as np
import pandas as pd
from collections import deque
from ta.volatility import BollingerBands
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BollingerReversionStrategy(BaseStrategy):

    STRAT_NUMBER = 6
    SYMBOL  = "QQQ"
    PERIOD  = 20
    STD_DEV = 2.0
    FORCED_STOP_LOSS_PCT = 0.02   # -2% (ajustado de 1.5%)
    FORCED_TAKE_PROFIT_PCT = 0.02 # +2% (nuevo)

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Bollinger Reversion",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=50)
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        self._has_position = qty > 0
        self._qty_bought = qty
        self._entry_price = 0.0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return


        self._closes.append(float(bar.close))

        if len(self._closes) < self.PERIOD:
            return

        s = pd.Series(list(self._closes))
        bb = BollingerBands(close=s, window=self.PERIOD, window_dev=self.STD_DEV)

        curr_price  = float(bar.close)
        curr_upper  = bb.bollinger_hband().iloc[-1]
        curr_middle = bb.bollinger_mavg().iloc[-1]
        curr_lower  = bb.bollinger_lband().iloc[-1]

        if pd.isna(curr_lower):
            return

        logger.info(
            f"[{self.name}] {bar.symbol} Precio={curr_price:.2f} "
            f"BB[{curr_lower:.2f} | {curr_middle:.2f} | {curr_upper:.2f}]"
        )

        # Salidas forzosas SL/TP — siempre evaluadas
        if self._has_position and self._entry_price > 0:
            ret = (curr_price / self._entry_price) - 1.0
            if ret <= -self.FORCED_STOP_LOSS_PCT:
                logger.info(f"[{self.name}] 🛑 SL FORZOSO {ret*100:+.2f}% → VENDIENDO")
                if hasattr(self.order_manager, 'sell_exact') and self._qty_bought > 0:
                    await self.order_manager.sell_exact(self.SYMBOL, self._qty_bought, strategy_name=self.name)
                else:
                    await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0
                self._qty_bought = 0.0
                self._entry_price = 0.0
                return
            if ret >= self.FORCED_TAKE_PROFIT_PCT:
                logger.info(f"[{self.name}] 💰 TP FORZOSO {ret*100:+.2f}% → VENDIENDO")
                if hasattr(self.order_manager, 'sell_exact') and self._qty_bought > 0:
                    await self.order_manager.sell_exact(self.SYMBOL, self._qty_bought, strategy_name=self.name)
                else:
                    await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0
                self._qty_bought = 0.0
                self._entry_price = 0.0
                return

        if curr_price <= curr_lower and not self._has_position:
            # Soft guard ANTES del log
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"):
                return

            logger.info(f"[{self.name}] 🟢 Precio tocó banda INFERIOR. COMPRANDO {self.SYMBOL}")

            # Estimación del notional dinámico para rastrear qty
            try:
                account = self.order_manager.client.get_account()
                settled_cash = float(getattr(account, 'settled_cash', account.cash if self.order_manager.paper else 0.0))
                regime_str = "UNKNOWN"
                if self.regime_manager:
                    from engine.regime_manager import get_current_regime
                    regime_str = get_current_regime().get("regime", "UNKNOWN")
                pct = {"BULL": 0.08, "CHOP": 0.05, "BEAR": 0.03, "UNKNOWN": 0.03}.get(regime_str, 0.02)
                dynamic_notional = round(settled_cash * pct, 2)
                self._qty_bought = dynamic_notional / curr_price
            except Exception as e:
                logger.error(f"[{self.name}] Error calculando notional estimado: {e}")
                self._qty_bought = 0.0

            queued = await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            if queued:
                self._has_position = True
                self._position[self.SYMBOL] = 1
                self._entry_price = curr_price

        elif self._has_position and curr_price >= curr_middle:
            logger.info(f"[{self.name}] 🔴 Precio llegó a la media. VENDIENDO {self._qty_bought} {self.SYMBOL}")
            if hasattr(self.order_manager, 'sell_exact') and self._qty_bought > 0:
                queued = await self.order_manager.sell_exact(self.SYMBOL, self._qty_bought, strategy_name=self.name)
            else:
                queued = await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
            if queued:
                self._has_position = False
                self._position[self.SYMBOL] = 0
                self._qty_bought = 0.0
                self._entry_price = 0.0

