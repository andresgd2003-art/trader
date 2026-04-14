"""
strat_04_pead.py — Post-Earnings Announcement Drift (PEAD)
============================================================
Régimen: BULL | Fuente: Alpaca News API
Timeframe: Daily | Hold: 14 días o hasta romper SMA50

Lógica (PEAD):
  Los mercados sub-reaccionan a sorpresas masivas de EPS.
  Condiciones de entrada:
    1. EPS reportado >20% por encima del consenso (requiere scraping/news)
    2. Gap up >5% en apertura del día del reporte
    3. Volumen >3x el promedio de 20 días
  Entry: Compra al cierre del día del earnings.
  Exit: 14 días después O cuando el precio rompa el SMA50 hacia abajo.
"""
import logging
from collections import deque
from datetime import timedelta
from ta.trend import SMAIndicator
import pandas as pd
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class PEADStrategy(BaseStrategy):
    STRAT_NUMBER = 4
    EPS_SURPRISE_MIN = 0.20   # >20% por encima del consenso
    GAP_MIN_PCT = 0.05         # Gap up >5%
    VOL_MULTIPLIER = 3.0       # Volumen >3x promedio 20D
    HOLD_DAYS = 14
    SMA50 = 50

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="PEAD Earnings Drift",
            # Universo dinámico: cualquier stock con earnings announcement
            symbols=["SPY"],  # placeholder — se actualiza desde news handler
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._closes: dict[str, deque] = {}
        self._volumes: dict[str, deque] = {}
        self._prev_close: dict[str, float] = {}
        self._positions: dict[str, dict] = {}  # {sym: {entry_date, entry_price, qty}}
        self._earnings_candidates: set = set()  # Symbols detectados por el news handler

    def flag_earnings_candidate(self, symbol: str):
        """Llamado desde el news handler cuando se detecta earnings surprise."""
        self._earnings_candidates.add(symbol)
        if symbol not in self.symbols:
            self.symbols.append(symbol)
        logger.info(f"[{self.name}] {symbol} marcado como candidato PEAD.")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='equities'):
            return

        sym = bar.symbol
        close = float(bar.close)
        vol = float(bar.volume)

        # Inicializar buffers si es nuevo símbolo
        if sym not in self._closes:
            self._closes[sym] = deque(maxlen=60)
            self._volumes[sym] = deque(maxlen=25)

        self._closes[sym].append(close)
        self._volumes[sym].append(vol)

        # ── Gestión de posiciones abiertas ──
        if sym in self._positions:
            pos = self._positions[sym]
            from datetime import datetime
            days_held = (datetime.now() - pos["entry_date"]).days

            # Exit: 14 días o SMA50 break
            if len(self._closes[sym]) >= self.SMA50:
                sma50 = pd.Series(list(self._closes[sym])).rolling(self.SMA50).mean().iloc[-1]
                if close < sma50 or days_held >= self.HOLD_DAYS:
                    reason = "SMA50 break" if close < sma50 else "14 días cumplidos"
                    logger.info(f"[{self.name}] EXIT {sym}: {reason}")
                    await self.order_manager.close_position(sym, None, self.name)
                    del self._positions[sym]
            return

        # ── Evaluación de entrada ──
        if sym not in self._earnings_candidates:
            return

        if len(self._volumes[sym]) < 20:
            return

        vol_avg = sum(list(self._volumes[sym])[-20:]) / 20
        prev_c = self._prev_close.get(sym, close * 0.95)
        gap_pct = (float(bar.open) - prev_c) / prev_c if prev_c > 0 else 0

        if gap_pct >= self.GAP_MIN_PCT and vol >= vol_avg * self.VOL_MULTIPLIER:
            # ⚠️ ANTI-DUPLICADO: Verificar posición viva para no re-entrar si reinició hoy
            if self.sync_position_from_alpaca(sym) > 0:
                logger.info(f"[{self.name}] ⚠️ PEAD en {sym} pero ya hay posición activa. Evitando duplicado.")
                self._earnings_candidates.discard(sym)
                return

            logger.info(
                f"[{self.name}] 📊 PEAD ENTRY {sym}: "
                f"Gap={gap_pct*100:.1f}% | Vol={vol:.0f} ({vol/vol_avg:.1f}x)"
            )
            await self.order_manager.buy_bracket(
                symbol=sym,
                price=close,
                stop_loss_pct=0.07,
                take_profit_pct=0.25,
                strategy_name=self.name
            )
            notional = self.order_manager._calculate_notional()
            self._positions[sym] = {
                "entry_date": datetime.now(),
                "entry_price": close,
                "notional": notional,
            }
            self._earnings_candidates.discard(sym)

    def on_bar_close(self, symbol: str, close: float):
        """Actualizar cierre del día anterior."""
        self._prev_close[symbol] = close
