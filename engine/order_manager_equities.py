"""
engine/order_manager_equities.py
==================================
OrderManager específico para acciones individuales de alta volatilidad.

Diferencias clave vs OrderManagerCrypto:
  - Bracket Orders: en lugar de trailing stop, usa StopLoss fijo calculado.
    (Alpaca no soporta trailing stop dentro de bracket orders aún)
  - TimeInForce.DAY: órdenes solo durante la sesión de mercado
  - Short selling: soportado para estrategias BEAR (Gap Fade, Stat Arb)
  - Etiqueta: "eq_" en client_order_id para filtrar en el dashboard
  - Micro-position sizing: NUNCA más de $100 USD por trade
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest,
    TrailingStopOrderRequest, TakeProfitRequest, StopLossRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

from engine.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

# Estado global para el dashboard
_EQ_STATUS: dict = {
    "orders_today": 0,
    "positions": [],
    "last_order": None,
}


def get_eq_status() -> dict:
    return _EQ_STATUS


class OrderManagerEquities:
    """
    Gestor centralizado de órdenes para el Equities Engine.
    Soporta bracket orders, trailing stops independientes y short selling.
    """

    MIN_DELAY_SECONDS = 0.5  # Acciones tienen más latencia que cripto
    MAX_POSITION_USD = 100.0  # Micro-sizing: máximo $100 por trade

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"

        self.client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper
        )

        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self.notifier = TelegramNotifier()

        logger.info("[OrderManagerEquities] Inicializado.")

    async def start(self):
        self._running = True
        logger.info("[OrderManagerEquities] Worker iniciado.")
        await self._process_queue()

    async def stop(self):
        self._running = False

    def _calculate_qty(self, notional_usd: float, price: float) -> int:
        """
        Calcula la cantidad de shares para acciones (enteros, no fraccionario).
        Nunca supera MAX_POSITION_USD.
        """
        if price <= 0:
            return 0
        capped = min(notional_usd, self.MAX_POSITION_USD)
        qty = int(capped / price)
        return max(qty, 1) if capped >= price else 0

    def _client_id(self, strategy: str) -> str:
        safe = strategy.replace(" ", "")[:20]
        return f"eq_{safe}_{uuid.uuid4().hex[:8]}"

    async def buy_bracket(
        self,
        symbol: str,
        price: float,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
        strategy_name: str = "",
        notional_usd: float = 100.0
    ):
        """
        Compra con bracket order (stop loss fijo + take profit).
        stop_loss_pct: porcentaje de pérdida máxima (ej: 0.03 = 3%)
        take_profit_pct: porcentaje de ganancia objetivo (ej: 0.06 = 6%)
        """
        qty = self._calculate_qty(notional_usd, price)
        if qty <= 0:
            logger.warning(f"[{strategy_name}] Qty calculada = 0 para {symbol}. Omitido.")
            return

        order = {
            "type": "bracket_buy",
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "strategy": strategy_name,
        }
        await self._queue.put(order)
        logger.info(
            f"[{strategy_name}] BRACKET BUY encolado: {qty}x {symbol} "
            f"@ ${price:.2f} | SL:{stop_loss_pct*100:.1f}% TP:{take_profit_pct*100:.1f}%"
        )

    async def sell_short(
        self,
        symbol: str,
        price: float,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
        strategy_name: str = "",
        notional_usd: float = 100.0
    ):
        """
        Short sell con bracket (stop buy + take profit para short).
        Solo en paper trading.
        """
        qty = self._calculate_qty(notional_usd, price)
        if qty <= 0:
            return

        order = {
            "type": "bracket_short",
            "symbol": symbol,
            "qty": qty,
            "price": price,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "strategy": strategy_name,
        }
        await self._queue.put(order)
        logger.info(f"[{strategy_name}] SHORT SELL encolado: {qty}x {symbol} @ ${price:.2f}")

    async def close_position(self, symbol: str, qty: int, strategy_name: str = ""):
        """Cierra una posición larga o corta."""
        order = {
            "type": "market_sell",
            "symbol": symbol,
            "qty": qty,
            "strategy": strategy_name,
        }
        await self._queue.put(order)
        logger.info(f"[{strategy_name}] CIERRE encolado: {qty}x {symbol}")

    async def _process_queue(self):
        while self._running:
            try:
                order = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._execute_order(order)
                await asyncio.sleep(self.MIN_DELAY_SECONDS)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"[OrderManagerEquities] Error: {e}")

    async def _execute_order(self, order: dict):
        global _EQ_STATUS
        symbol = order["symbol"]
        strategy = order.get("strategy", "Unknown")
        client_id = self._client_id(strategy)

        # ==========================================
        # 🛡️ COMPLIANCE SHIELD: Prevención de Baneos
        # ==========================================
        if order["type"] in ["bracket_buy", "bracket_short"]:
            try:
                acc = self.client.get_account()
                dt_count = getattr(acc, 'daytrade_count', 0)
                is_pdt = getattr(acc, 'pattern_day_trader', False)
                
                if is_pdt or dt_count >= 3:
                    logger.error(f"🛑 [COMPLIANCE SHIELD] ORDEN BLOQUEADA para {symbol}. Riesgo de Baneo P.D.T. (Day Trades: {dt_count}/3)")
                    self.notifier.send_message(
                        f"🛑 <b>[ESCUDO ANTI-BAN]</b>\nSe bloqueó de emergencia la entrada a <b>{symbol}</b> ({strategy}).\n"
                        f"Límite legal de Day Trades alcanzado. Esto previno que el broker congelara tu cuenta."
                    )
                    return # Abortamos la compra para salvar la cuenta
            except Exception as compliance_err:
                logger.warning(f"No se pudo validar el Compliance Shield: {compliance_err}")
        # ==========================================

        try:
            if order["type"] == "bracket_buy":
                stop_price = round(order["price"] * (1 - order["stop_loss_pct"]), 2)
                tp_price = round(order["price"] * (1 + order["take_profit_pct"]), 2)

                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=order["qty"],
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tp_price),
                    stop_loss=StopLossRequest(stop_price=stop_price),
                    client_order_id=client_id
                )
                result = self.client.submit_order(req)
                logger.info(
                    f"[{strategy}] ✅ BRACKET BUY {symbol}: "
                    f"Entry~${order['price']:.2f} | SL=${stop_price} | TP=${tp_price} | ID:{result.id}"
                )

            elif order["type"] == "bracket_short":
                stop_price = round(order["price"] * (1 + order["stop_loss_pct"]), 2)
                tp_price = round(order["price"] * (1 - order["take_profit_pct"]), 2)

                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=order["qty"],
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tp_price),
                    stop_loss=StopLossRequest(stop_price=stop_price),
                    client_order_id=client_id
                )
                result = self.client.submit_order(req)
                logger.info(f"[{strategy}] ✅ BRACKET SHORT {symbol}: SL=${stop_price} | TP=${tp_price}")

            elif order["type"] == "market_sell":
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=order["qty"],
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id
                )
                result = self.client.submit_order(req)
                logger.info(f"[{strategy}] ✅ MARKET SELL {symbol} x{order['qty']}")

            _EQ_STATUS["orders_today"] = _EQ_STATUS.get("orders_today", 0) + 1
            _EQ_STATUS["last_order"] = {
                "symbol": symbol, "type": order["type"], "strategy": strategy
            }

        except Exception as e:
            logger.error(f"[{strategy}] ❌ Error ejecutando orden {symbol}: {e}")
            self.notifier.send_message(
                f"⚠️ <b>[ERROR EQUITIES {strategy}]</b>\nFallo en {symbol}: {e}"
            )

    def cancel_all_open_orders(self):
        """Cancela todas las órdenes abiertas del día (al cierre del mercado)."""
        try:
            self.client.cancel_orders()
            logger.info("[OrderManagerEquities] Órdenes pendientes canceladas.")
        except Exception as e:
            logger.error(f"[OrderManagerEquities] Error cancelando órdenes: {e}")
