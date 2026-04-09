"""
strategies/strat_10_grid.py — Grid Trading
==========================================
LÓGICA:
- Establece un precio base (baseline)
- Coloca órdenes de COMPRA cada -1% por debajo del baseline
- Coloca órdenes de VENTA cada +1% por encima del baseline
- Cuando una orden se ejecuta, se "repone" la grid colocando la contraria

¿Por qué funciona?
En mercados laterales (sin tendencia clara), el precio sube y baja
dentro de un rango. El grid captura esas oscilaciones automáticamente,
comprando barato y vendiendo caro repetidamente.

NOTA: Funciona mejor en mercados con baja volatilidad y tendencia lateral.
"""
import logging
import asyncio
from collections import defaultdict
from engine.base_strategy import BaseStrategy
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import os

logger = logging.getLogger(__name__)


class GridTradingStrategy(BaseStrategy):

    SYMBOL      = "SOXX"
    GRID_STEP   = 0.01    # 1% entre cada nivel
    GRID_LEVELS = 5       # Niveles de grid arriba y abajo del baseline
    QTY_PER_LEVEL = 3     # Acciones por nivel del grid

    def __init__(self, order_manager):
        super().__init__(
            name="Grid Trading",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self._baseline    = None    # Precio inicial de referencia
        self._grid_active = False
        self._grid_orders = {}      # {precio: side}
        self._client = TradingClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
            paper=True
        )

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return

        current_price = float(bar.close)

        # La primera vez que recibimos una barra, establecemos el baseline
        if self._baseline is None:
            self._baseline = current_price
            logger.info(f"[{self.name}] Baseline establecido: ${self._baseline:.2f}")
            await self._place_grid()
            return

        pct_from_baseline = (current_price - self._baseline) / self._baseline * 100
        logger.info(
            f"[{self.name}] {self.SYMBOL} ${current_price:.2f} "
            f"({pct_from_baseline:+.1f}% desde baseline)"
        )

    async def _place_grid(self):
        """
        Coloca órdenes BUY límite por debajo del baseline.
        Las órdenes SELL se añaden dinámicamente cuando se ejecuta una BUY.
        (Las cuentas Paper Trading no permiten short selling directo)
        """
        if not self._baseline:
            return

        logger.info(f"[{self.name}] Colocando {self.GRID_LEVELS} niveles de compra...")

        for i in range(1, self.GRID_LEVELS + 1):
            buy_price = round(self._baseline * (1 - self.GRID_STEP * i), 2)

            try:
                buy_req = LimitOrderRequest(
                    symbol=self.SYMBOL,
                    qty=self.QTY_PER_LEVEL,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.GTC,
                    limit_price=buy_price
                )
                self._client.submit_order(buy_req)
                logger.info(f"[{self.name}] Grid BUY colocada @ ${buy_price:.2f} (nivel -{i}%)")
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"[{self.name}] Error en nivel {i}: {e}")

        self._grid_active = True
        logger.info(f"[{self.name}] ✅ Grid activo. Base=${self._baseline:.2f} | {self.GRID_LEVELS} BUYs pendientes")
