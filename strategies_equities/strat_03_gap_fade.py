"""
strat_03_gap_fade.py — Opening Gap Fade (Short Sell)
=====================================================
Régimen: BEAR | Universo: Dynamic Top Gainers
Timeframe: 1-min bars | Ventana: 09:30-10:30 AM EST

Lógica:
  Stocks pequeños que gapan >30% por arriba en "no news" suelen colapsar.
  1. Si el gap up de apertura es >30% vs cierre anterior
  2. La primera vela de 5min es bajista (close < open)
  3. El precio rompe abajo del VWAP de la vela de apertura
  → SHORT SELL
  Target: cierre de ayer.
  Stop: High del día.
"""
import logging
from datetime import time as dtime
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

MIN_GAP_PCT = 0.30  # Gap mínimo del 30%


class GapFadeStrategy(BaseStrategy):
    STRAT_NUMBER = 3

    def __init__(self, order_manager, regime_manager=None, symbols: list = None):
        super().__init__(
            name="Opening Gap Fade",
            symbols=symbols or [],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._prev_close: dict[str, float] = {}
        self._first_bar: dict[str, object] = {}
        self._day_high: dict[str, float] = {}
        self._day_vwap: dict[str, float] = {}
        self._cum_pv: dict[str, float] = {}
        self._cum_vol: dict[str, float] = {}
        self._traded: set = set()

    def update_symbols(self, new_symbols: list, prev_closes: dict = None):
        self.symbols = new_symbols
        self._prev_close = prev_closes or {}
        self._first_bar = {}
        self._day_high = {}
        self._traded = set()

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER):
            return

        sym = bar.symbol
        bar_time = bar.timestamp.time() if hasattr(bar.timestamp, 'time') else dtime(9, 30)

        if not (dtime(9, 30) <= bar_time <= dtime(10, 30)):
            return
        if sym in self._traded:
            return

        close = float(bar.close)
        high = float(bar.high)
        open_ = float(bar.open)
        vol = float(bar.volume)

        # Actualizar VWAP intraday
        typ = (high + float(bar.low) + close) / 3.0
        self._cum_pv[sym] = self._cum_pv.get(sym, 0) + typ * vol
        self._cum_vol[sym] = self._cum_vol.get(sym, 0) + vol
        vwap = self._cum_pv[sym] / self._cum_vol[sym] if self._cum_vol[sym] > 0 else close

        # Actualizar High del día
        self._day_high[sym] = max(self._day_high.get(sym, 0), high)

        # Primera vela — registrar
        if sym not in self._first_bar:
            self._first_bar[sym] = bar
            return  # Esperar 2da barra para confirmar

        # Verificar gap >30%
        prev_c = self._prev_close.get(sym, 0)
        if prev_c <= 0:
            return

        gap_pct = (open_ - prev_c) / prev_c
        if gap_pct < MIN_GAP_PCT:
            return

        # Primera vela bajista?
        first_bar = self._first_bar[sym]
        is_bearish = float(first_bar.close) < float(first_bar.open)

        # Precio rompió por debajo del VWAP?
        below_vwap = close < vwap

        if is_bearish and below_vwap:
            day_high = self._day_high.get(sym, close * 1.05)
            stop_pct = (day_high - close) / close  # SL = High del día
            tp_pct = (close - prev_c) / close       # TP = cierre de ayer

            logger.info(
                f"[{self.name}] 📉 GAP FADE {sym}! "
                f"Gap={gap_pct*100:.1f}% | Close={close:.2f} < VWAP={vwap:.2f}"
            )
            await self.order_manager.sell_short(
                symbol=sym,
                price=close,
                stop_loss_pct=min(stop_pct, 0.10),   # Cap SL en 10%
                take_profit_pct=min(tp_pct, 0.30),   # Cap TP en 30%
                strategy_name=self.name
            )
            self._traded.add(sym)

    def on_market_open(self):
        self._first_bar = {}
        self._day_high = {}
        self._cum_pv = {}
        self._cum_vol = {}
        self._traded = set()
