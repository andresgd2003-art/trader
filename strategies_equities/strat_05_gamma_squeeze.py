"""
strat_05_gamma_squeeze.py — Gamma Squeeze Proxy
================================================
Régimen: BULL | Universo: High Short Interest >20% (lista curada)
Timeframe: Daily / 1H

Lógica (Meme Stock / Gamma Squeeze proxy):
  1. Buscar stocks con short interest alto (datos de FINRA/Yahoo aproximados)
  2. Si precio cruza SMA20 al alza con spike de volumen >300%
  3. El setup sugiere cobertura forzada de shorts → COMPRA

La lista de short squeeze candidates se actualiza semanalmente
(hardcoded como proxy sin API de short interest).

Tight trailing stop: la posición puede beneficiarse de movimientos explosivos.
"""
import logging
from collections import deque
import pandas as pd
from ta.trend import SMAIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

# Stocks históricamente vinculados a high short interest
# (actualizar semanalmente en producción)
SHORT_SQUEEZE_CANDIDATES = [
    "GME", "AMC", "BBBY", "MVIS", "CLOV", "WKHS",
    "NKLA", "RIDE", "GOEV", "LCID", "RIVN", "SPCE",
    "SNDL", "TLRY", "ATER", "CEI", "PROG"
]


class GammaSqueezeStrategy(BaseStrategy):
    STRAT_NUMBER = 5
    SMA_PERIOD = 20
    VOL_MULTIPLIER = 3.0    # 300% del promedio
    STOP_LOSS_PCT = 0.05    # Tight: 5%
    TAKE_PROFIT_PCT = 0.30  # Upside explosivo: 30%
    MIN_PRICE = 1.05        # Evita penny stocks (rutina _adopt_orphan_positions liquida <$1 al reiniciar)

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Gamma Squeeze",
            symbols=SHORT_SQUEEZE_CANDIDATES,
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes: dict[str, deque] = {s: deque(maxlen=30) for s in SHORT_SQUEEZE_CANDIDATES}
        self._volumes: dict[str, deque] = {s: deque(maxlen=25) for s in SHORT_SQUEEZE_CANDIDATES}
        self._prev_above_sma: dict[str, bool] = {}
        self._traded_today: set = set()

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
        if bar.symbol in self._traded_today:
            return

        sym = bar.symbol
        close = float(bar.close)
        vol = float(bar.volume)

        self._closes[sym].append(close)
        self._volumes[sym].append(vol)

        if len(self._closes[sym]) < self.SMA_PERIOD:
            return

        # Filtro anti-penny: evita compras <$1.05 que serían liquidadas al reiniciar
        if close < self.MIN_PRICE:
            return

        closes = pd.Series(list(self._closes[sym]))
        sma20 = SMAIndicator(closes, window=self.SMA_PERIOD).sma_indicator().iloc[-1]

        above_sma = close > sma20
        was_above = self._prev_above_sma.get(sym, False)

        # Detección de cruce SMA20 al alza
        if above_sma and not was_above:
            vol_avg = sum(list(self._volumes[sym])[:-1]) / max(len(self._volumes[sym]) - 1, 1)

            if vol_avg > 0 and vol >= vol_avg * self.VOL_MULTIPLIER:
                # ⚠️ ANTI-DUPLICADO: Verificar posición viva para no re-entrar si reinició hoy
                if self.sync_position_from_alpaca(sym) > 0:
                    logger.info(f"[{self.name}] ⚠️ Spread Gamma detectado en {sym} pero ya hay posición activa. Evitando duplicado.")
                    self._traded_today.add(sym)
                    return

                logger.info(
                    f"[{self.name}] 🔥 GAMMA SQUEEZE signal {sym}! "
                    f"Close={close:.2f} > SMA20={sma20:.2f} | "
                    f"Vol={vol:.0f} ({vol/vol_avg:.1f}x)"
                )
                if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="equities"): return
                await self.order_manager.buy_bracket(
                    symbol=sym,
                    price=close,
                    stop_loss_pct=self.STOP_LOSS_PCT,
                    take_profit_pct=self.TAKE_PROFIT_PCT,
                    strategy_name=self.name
                )
                self._traded_today.add(sym)

        self._prev_above_sma[sym] = above_sma

    def on_market_open(self):
        self._traded_today = set()
