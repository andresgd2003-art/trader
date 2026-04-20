"""
strategies/strat_08_vwap.py — VWAP Bounce (Intraday)
=====================================================
LÓGICA:
- Calcula el VWAP (precio promedio del día ponderado por volumen)
- COMPRA si el precio cruza VWAP desde abajo Y el volumen actual
  supera el promedio de las últimas 15 barras (confirmación de momentum)
- CIERRA la posición automáticamente a las 15:50 EST (antes del cierre)

¿Qué es el VWAP?
Volume Weighted Average Price = promedio del precio intraday
ponderado por cuánto se ha negociado. Es el precio "justo" del día.
Cuando el precio rebota desde abajo del VWAP con volumen alto,
es una señal de que los compradores tienen fuerza.
"""
import logging
import numpy as np
import asyncio
from collections import deque
from datetime import datetime, timezone
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class VWAPBounceStrategy(BaseStrategy):

    SYMBOL      = "SMH"
    VOL_BARS    = 15    # Promedio de volumen de las últimas N barras
    CLOSE_HOUR  = 20    # 20:50 UTC = 15:50 EST
    CLOSE_MIN   = 50
    QTY         = 20

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="VWAP Bounce",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        # Para VWAP acumulado del día
        self._cumulative_tp_vol = 0.0   # Σ (precio_típico × volumen)
        self._cumulative_vol    = 0.0   # Σ volumen
        self._last_date         = None  # Para resetear al nuevo día

        self._volumes = deque(maxlen=self.VOL_BARS)
        self._prev_price_below_vwap = None
        self._has_position = False
        self._loop_started = False  # Se inicia en el primer on_bar

    def _reset_vwap(self):
        self._cumulative_tp_vol = 0.0
        self._cumulative_vol    = 0.0
        logger.info(f"[{self.name}] VWAP reseteado para nuevo día.")

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        # Iniciar EOD close loop la primera vez que llegue una barra
        if not self._loop_started:
            asyncio.create_task(self._eod_close_loop())
            self._loop_started = True

        # Resetear VWAP si es un nuevo día
        bar_date = bar.timestamp.date() if hasattr(bar.timestamp, 'date') else None
        if bar_date and bar_date != self._last_date:
            self._reset_vwap()
            self._last_date = bar_date

        # Precio típico = (High + Low + Close) / 3
        typical_price = (float(bar.high) + float(bar.low) + float(bar.close)) / 3
        volume = float(bar.volume)

        self._cumulative_tp_vol += typical_price * volume
        self._cumulative_vol    += volume
        self._volumes.append(volume)

        if self._cumulative_vol == 0:
            return

        vwap = self._cumulative_tp_vol / self._cumulative_vol
        current_price = float(bar.close)
        avg_volume = np.mean(self._volumes) if len(self._volumes) >= 3 else 0

        price_below_vwap = current_price < vwap

        logger.info(
            f"[{self.name}] {bar.symbol} Precio={current_price:.2f} "
            f"VWAP={vwap:.2f} Vol={volume:.0f} AvgVol={avg_volume:.0f}"
        )

        # Cruce: precio pasa de DEBAJO a ARRIBA del VWAP con volumen alto
        if (self._prev_price_below_vwap is True
                and not price_below_vwap
                and volume > avg_volume
                and not self._has_position):
            logger.info(f"[{self.name}] 🟢 Precio cruzó VWAP desde abajo con volumen alto! COMPRANDO {self.SYMBOL}")
            await self.order_manager.buy(self.SYMBOL, strategy_name=self.name)
            self._has_position = True
            self._position[self.SYMBOL] = self.QTY

        self._prev_price_below_vwap = price_below_vwap

    async def _eod_close_loop(self):
        """Cierra posición automáticamente a las 15:50 EST (20:50 UTC)."""
        while self.is_active:
            now = datetime.now(timezone.utc)
            if (now.hour == self.CLOSE_HOUR and now.minute >= self.CLOSE_MIN
                    and self._has_position):
                logger.info(f"[{self.name}] 🔔 15:50 EST → Cerrando posición intraday.")
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                self._has_position = False
                self._position[self.SYMBOL] = 0
            await asyncio.sleep(60)
