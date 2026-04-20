"""
strat_06_rsi_extreme.py — Extreme RSI Mean Reversion
=====================================================
Régimen: BEAR | Universo: Dynamic Top Losers (scanner)
Timeframe: 5-min bars

Lógica (Flash Crash Bounce):
  1. Calcular RSI de 4 períodos en barras de 5 minutos
  2. Calcular Bollinger Band de 20 períodos
  3. Si RSI(4) < 10 Y precio toca la Lower Bollinger Band → COMPRA el rebote
  4. Exit: RSI(4) cruza >65

Es una de las estrategias más agresivas — captura dips extremos intraday.
"""
import logging
from collections import deque
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

RSI_EXTREME_LOW  = 10.0   # RSI <10 = extremadamente sobrevendido
RSI_EXIT_LEVEL   = 65.0   # Salir cuando RSI >65
BB_PERIOD        = 20
BB_STD           = 2.0
RSI_PERIOD       = 4


class RSIExtremeStrategy(BaseStrategy):
    STRAT_NUMBER = 6

    def __init__(self, order_manager, regime_manager=None, symbols: list = None):
        super().__init__(
            name="RSI Extreme Reversion",
            symbols=symbols or [],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes: dict[str, deque] = {}
        self._in_position: dict[str, bool] = {}
        self._position_qty: dict[str, int] = {}

    def update_symbols(self, new_symbols: list):
        self.symbols = new_symbols
        for sym in new_symbols:
            if sym not in self._closes:
                self._closes[sym] = deque(maxlen=50)
                self._in_position[sym] = False
                self._position_qty[sym] = 0

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        sym = bar.symbol
        close = float(bar.close)

        if sym not in self._closes:
            self._closes[sym] = deque(maxlen=50)
            self._in_position[sym] = False
            self._position_qty[sym] = 0

        self._closes[sym].append(close)

        if len(self._closes[sym]) < BB_PERIOD + RSI_PERIOD:
            return

        closes = pd.Series(list(self._closes[sym]))
        rsi = RSIIndicator(closes, window=RSI_PERIOD).rsi().iloc[-1]
        bb = BollingerBands(closes, window=BB_PERIOD, window_dev=BB_STD)
        lower_bb = bb.bollinger_lband().iloc[-1]

        # ── Gestión de posición abierta ──
        if self._in_position.get(sym):
            if rsi > RSI_EXIT_LEVEL:
                logger.info(f"[{self.name}] EXIT {sym}: RSI subió a {rsi:.1f}")
                await self.order_manager.close_position(
                    sym, self._position_qty[sym], self.name
                )
                self._in_position[sym] = False
                self._position_qty[sym] = 0
            return

        # ── Evaluación de entrada ──
        if rsi < RSI_EXTREME_LOW and close <= lower_bb:
            logger.info(
                f"[{self.name}] ⚡ FLASH CRASH BOUNCE {sym}! "
                f"RSI={rsi:.1f} < 10 | Close={close:.2f} <= BB_Low={lower_bb:.2f}"
            )
            await self.order_manager.buy_bracket(
                symbol=sym,
                price=close,
                stop_loss_pct=0.05,
                take_profit_pct=0.10,
                strategy_name=self.name
            )
            self._in_position[sym] = True
            self._position_qty[sym] = self.order_manager._calculate_qty(100.0, close)

    def on_market_open(self):
        self._in_position = {s: False for s in self.symbols}
        self._position_qty = {s: 0 for s in self.symbols}
