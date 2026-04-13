"""
strat_01_gapper_mom.py — Pre-Market Gapper Momentum
======================================================
Régimen: BULL | Universo: Dynamic Top Gainers ($1-$25)
Timeframe: 1-min bars | Ventana: 09:30-10:00 AM EST

Lógica:
  1. Esperar las primeras 5 velas de 1min (09:30-09:34)
  2. Trackear el High de esas 5 velas
  3. Si la vela de 09:35 rompe ese High con volumen extremo (>3x) → BUY
  4. Trailing stop del 2.5% (bracket order con SL fijo)

Risk: ALTA. Estos stocks pueden colapsar en segundos.
      Micro-sizing forzado: $100 max via OrderManagerEquities.
"""
import logging
from collections import deque
from datetime import time as dtime
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class GapperMomentumStrategy(BaseStrategy):
    STRAT_NUMBER = 1
    STOP_LOSS_PCT = 0.025    # 2.5% stop
    TAKE_PROFIT_PCT = 0.10   # 10% target (asimétrico 1:4)
    VOL_MULTIPLIER = 3.0     # Volumen mínimo 3x el promedio

    def __init__(self, order_manager, regime_manager=None, symbols: list = None):
        super().__init__(
            name="Gapper Momentum",
            symbols=symbols or [],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        # Por cada símbolo: deque de las primeras 5 velas
        self._opening_bars: dict[str, list] = {}
        self._five_min_high: dict[str, float] = {}
        self._five_min_vol_avg: dict[str, float] = {}
        self._traded_today: set = set()

    def update_symbols(self, new_symbols: list):
        """Actualiza el universo dinámico del día."""
        self.symbols = new_symbols
        self._opening_bars = {}
        self._five_min_high = {}
        self._traded_today = set()
        logger.info(f"[{self.name}] Universo actualizado: {new_symbols}")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='equities'):
            return

        symbol = bar.symbol
        bar_time = bar.timestamp.time() if hasattr(bar.timestamp, 'time') else dtime(9, 30)

        # Solo opera de 09:30 a 10:00 AM
        if not (dtime(9, 30) <= bar_time <= dtime(10, 0)):
            return
        if symbol in self._traded_today:
            return

        # Acumular las primeras 5 velas
        if symbol not in self._opening_bars:
            self._opening_bars[symbol] = []

        if len(self._opening_bars[symbol]) < 5:
            self._opening_bars[symbol].append(bar)
            if len(self._opening_bars[symbol]) == 5:
                # Calcular High y vol promedio de las primeras 5 velas
                first_five = self._opening_bars[symbol]
                self._five_min_high[symbol] = max(b.high for b in first_five)
                self._five_min_vol_avg[symbol] = sum(b.volume for b in first_five) / 5
                logger.debug(
                    f"[{self.name}] {symbol}: 5min High=${self._five_min_high[symbol]:.2f} "
                    f"VolAvg={self._five_min_vol_avg[symbol]:.0f}"
                )
            return

        # A partir de la vela 6 en adelante — buscar breakout
        five_high = self._five_min_high.get(symbol, 0)
        vol_avg = self._five_min_vol_avg.get(symbol, 1)

        if bar.high > five_high and bar.volume > (vol_avg * self.VOL_MULTIPLIER):
            logger.info(
                f"[{self.name}] 🚀 {symbol} BREAKOUT! "
                f"High={bar.high:.2f} > 5minHigh={five_high:.2f} | "
                f"Vol={bar.volume:.0f} ({bar.volume/vol_avg:.1f}x)"
            )
            await self.order_manager.buy_bracket(
                symbol=symbol,
                price=float(bar.close),
                stop_loss_pct=self.STOP_LOSS_PCT,
                take_profit_pct=self.TAKE_PROFIT_PCT,
                strategy_name=self.name
            )
            self._traded_today.add(symbol)

    def on_market_open(self):
        """Resetear al inicio de cada sesión."""
        self._opening_bars = {}
        self._five_min_high = {}
        self._five_min_vol_avg = {}
        self._traded_today = set()
