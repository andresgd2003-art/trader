"""
strategies/strat_10_grid.py — Grid Trading SOXX (Cash Account Compatible)
==========================================================================
LÓGICA COMPLETA:
- Establece un precio base (baseline) al detectar la primera barra
- Coloca órdenes Limit de COMPRA escalonadas cada -3% debajo del baseline
- Monitorea posición real: si SOXX sube +3% desde baseline, VENDE (take profit)
- Si el precio drifa >5%, recalibra la grid

CPU OPTIMIZATION:
- Logging throttled a cada 5 minutos (no cada barra)
"""
import logging
import asyncio
import time
from engine.base_strategy import BaseStrategy
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import os
import uuid

logger = logging.getLogger(__name__)


class GridTradingStrategy(BaseStrategy):

    STRAT_NUMBER = 10
    SYMBOL      = "SOXX"
    GRID_STEP   = 0.03    # 3% entre cada nivel
    GRID_LEVELS = 5       # Niveles de grid
    TAKE_PROFIT = 0.03    # Vender si sube 3% desde baseline
    LOG_INTERVAL = 300    # Loguear cada 5 minutos

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Grid Trading",
            symbols=[self.SYMBOL],
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._baseline    = None
        self._grid_active = False
        self._last_log_time = 0
        self._client = TradingClient(
            api_key=os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", ""),
            paper=True
        )

    async def on_bar(self, bar) -> None:
        if not self.should_process(bar.symbol):
            return
        current_price = float(bar.close)

        if self._baseline is None:
            self._baseline = current_price
            logger.info(f"[{self.name}] Baseline establecido: ${self._baseline:.2f}")
            if not self.check_open_orders_exist(self.SYMBOL):
                await self._place_grid()
            else:
                self._grid_active = True
            return

        pct_from_baseline = (current_price - self._baseline) / self._baseline

        # Logging throttled
        now = time.time()
        if now - self._last_log_time >= self.LOG_INTERVAL:
            logger.info(
                f"[{self.name}] {self.SYMBOL} ${current_price:.2f} "
                f"({pct_from_baseline*100:+.1f}% desde baseline)"
            )
            self._last_log_time = now

        # ── TAKE PROFIT: Si el precio sube 3%+ desde el baseline, vender ──
        if pct_from_baseline >= self.TAKE_PROFIT:
            qty = self.sync_position_from_alpaca(self.SYMBOL)
            if qty > 0:
                logger.info(
                    f"[{self.name}] 💰 TAKE PROFIT: SOXX ${current_price:.2f} "
                    f"(+{pct_from_baseline*100:.1f}% desde ${self._baseline:.2f}). "
                    f"Vendiendo {qty} shares."
                )
                await self.order_manager.sell(self.SYMBOL, strategy_name=self.name)
                # Recalibrar baseline al nuevo precio
                self._baseline = current_price
                self._grid_active = False
                # Redesplegar grid desde el nuevo baseline
                await self._place_grid()

        # ── RECALIBRACIÓN: Si el precio drifa >5%, redesplegar grid ──
        elif abs(pct_from_baseline) > 0.05 and self._grid_active:
            logger.info(
                f"[{self.name}] 🔄 Drift >5%. Recalibrando baseline "
                f"de ${self._baseline:.2f} a ${current_price:.2f}"
            )
            self._baseline = current_price
            self._grid_active = False
            await self._place_grid()

    async def _place_grid(self):
        if not self._baseline:
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="etf"):
            return

        logger.info(f"[{self.name}] 🏗️ Colocando {self.GRID_LEVELS} niveles de compra (Paso 3%)...")

        try:
            acc = self._client.get_account()
            cash_ref = float(getattr(acc, 'settled_cash', acc.cash))
            notional_per_level = round(cash_ref * 0.04, 2)
            if notional_per_level < 1.0: notional_per_level = 10.0
        except:
            notional_per_level = 10.0

        for i in range(1, self.GRID_LEVELS + 1):
            buy_price = round(self._baseline * (1 - self.GRID_STEP * i), 2)

            try:
                qty_calculated = max(1, int(notional_per_level // buy_price))
                buy_req = LimitOrderRequest(
                    symbol=self.SYMBOL,
                    qty=qty_calculated,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=buy_price,
                    client_order_id=f"strat_{self.name.replace(' ','')}_{uuid.uuid4().hex[:8]}"
                )
                self._client.submit_order(buy_req)
                logger.info(f"[{self.name}] Grid BUY @ ${buy_price:.2f} (${notional_per_level})")
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"[{self.name}] Error en nivel {i}: {e}")

        self._grid_active = True
        logger.info(f"[{self.name}] ✅ Grid activa. Base=${self._baseline:.2f}")
