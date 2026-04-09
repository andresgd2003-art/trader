"""
strategies/strat_03_rotation.py — Momentum Rotation Semanal
============================================================
LÓGICA:
- Cada viernes a las 15:30 EST evalúa 4 ETFs: QQQ, SMH, XLK, SRVR
- Calcula el rendimiento (%) de cada uno en los últimos 30 días
- Liquida la posición actual y compra 100% del ETF con mejor rendimiento

¿Por qué funciona?
El momentum sectorial tiende a persistir. El ETF que ha sido el
más fuerte en el último mes probablemente siga siendo el más fuerte
la semana siguiente.
"""
import logging
import asyncio
from datetime import datetime, timezone
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import os
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class MomentumRotationStrategy(BaseStrategy):

    UNIVERSE = ["QQQ", "SMH", "XLK", "SRVR"]
    LOOKBACK_DAYS = 30

    def __init__(self, order_manager):
        super().__init__(
            name="Momentum Rotation",
            symbols=self.UNIVERSE,
            order_manager=order_manager
        )
        self._current_holding = None
        self._data_client = StockHistoricalDataClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", "")
        )
        self._loop_started = False  # Se inicia en el primer on_bar

    async def on_bar(self, bar) -> None:
        # Iniciar el loop semanal la primera vez que llegue una barra
        if not self._loop_started:
            asyncio.create_task(self._weekly_rotation_loop())
            self._loop_started = True

    async def _weekly_rotation_loop(self):
        """Loop que corre cada hora y ejecuta rotación los viernes a las 15:30 EST."""
        while self.is_active:
            now_utc = datetime.now(timezone.utc)
            # Viernes = weekday 4, 20:30 UTC = 15:30 EST (sin DST) / 15:30 EDT = 19:30 UTC
            if now_utc.weekday() == 4 and now_utc.hour == 20 and now_utc.minute >= 30:
                logger.info(f"[{self.name}] Ejecutando rotación semanal...")
                await self._rotate()
            await asyncio.sleep(3600)  # Revisar cada hora

    async def _rotate(self):
        """Calcula el mejor ETF y rota el portafolio."""
        from datetime import timedelta
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=self.LOOKBACK_DAYS + 5)

        performances = {}
        for symbol in self.UNIVERSE:
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end
                )
                bars = self._data_client.get_stock_bars(request)
                df = bars.df
                if len(df) >= 2:
                    first_close = float(df.iloc[0]["close"])
                    last_close  = float(df.iloc[-1]["close"])
                    pct_change  = (last_close - first_close) / first_close * 100
                    performances[symbol] = pct_change
                    logger.info(f"[{self.name}] {symbol}: {pct_change:.2f}% en {self.LOOKBACK_DAYS}d")
            except Exception as e:
                logger.error(f"[{self.name}] Error obteniendo datos de {symbol}: {e}")

        if not performances:
            return

        best_symbol = max(performances, key=performances.get)
        best_pct    = performances[best_symbol]

        logger.info(f"[{self.name}] 🏆 Mejor ETF: {best_symbol} (+{best_pct:.2f}%)")

        # Liquidar posición actual si es diferente al ganador
        if self._current_holding and self._current_holding != best_symbol:
            logger.info(f"[{self.name}] Liquidando {self._current_holding}...")
            await self.order_manager.sell(self._current_holding, qty=50, strategy_name=self.name)

        # Comprar el ganador
        if best_symbol != self._current_holding:
            await self.order_manager.buy(best_symbol, qty=50, strategy_name=self.name)
            self._current_holding = best_symbol
            self._position = {best_symbol: 50}
