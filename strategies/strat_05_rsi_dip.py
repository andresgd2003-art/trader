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

    STRAT_NUMBER = 5
    SYMBOL     = "TQQQ"
    RSI_PERIOD = 14
    RSI_BUY    = 45   # Era 30 — sube para capturar correcciones moderadas en rally
    RSI_SELL   = 65   # Era 70 — baja para asegurar ganancia antes de sobrecompra
    # TQQQ es 3x apalancado — necesita stops más agresivos que un ETF normal
    STOP_LOSS_PCT   = 0.025  # -2.5% corta pérdidas antes de que el apalancamiento las amplifique
    TAKE_PROFIT_PCT = 0.05   # +5% cierra ganancia sin esperar al cruce RSI 65

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
        # Entry price para SL/TP — se inicializa desde Alpaca si hay posición
        self._entry_price = 0.0
        if self._has_position:
            try:
                pos = self.order_manager.client.get_open_position(self.SYMBOL)
                self._entry_price = float(pos.avg_entry_price)
                logger.info(f"[{self.name}] Entry price sincronizado desde Alpaca: ${self._entry_price:.2f}")
            except Exception:
                pass

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

        # Salidas evaluadas SIEMPRE (sin Soft Guard) para proteger capital ya invertido
        if self._has_position and self._entry_price > 0:
            ret = (float(bar.close) / self._entry_price) - 1.0
            if ret <= -self.STOP_LOSS_PCT:
                logger.info(f"[{self.name}] 🛑 STOP LOSS {ret*100:+.2f}% → VENDIENDO {self.SYMBOL}")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0
                self._entry_price = 0.0
                return
            if ret >= self.TAKE_PROFIT_PCT:
                logger.info(f"[{self.name}] 💰 TAKE PROFIT {ret*100:+.2f}% → VENDIENDO {self.SYMBOL}")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0
                self._entry_price = 0.0
                return

        if current_rsi < self.RSI_BUY and not self._has_position:
            logger.info(f"[{self.name}] 🟢 RSI={current_rsi:.1f} < {self.RSI_BUY} → COMPRANDO {self.SYMBOL}")
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"): return
            await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = 1
            self._entry_price = float(bar.close)

        elif current_rsi > self.RSI_SELL and self._has_position:
            logger.info(f"[{self.name}] 🔴 RSI={current_rsi:.1f} > {self.RSI_SELL} → VENDIENDO {self.SYMBOL}")
            await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
            self._has_position = False
            self._position[self.SYMBOL] = 0
            self._entry_price = 0.0
