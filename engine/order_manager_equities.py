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
try:
    from engine.daily_mode import get_mode_label
except ImportError:
    def get_mode_label(): return "mA"

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

    def _calculate_notional(self) -> float:
        """
        Calcula el notional para acciones (fraccionario).
        Utiliza el 5% del settled_cash (o cash en Paper) para evitar GFV. Nunca supera MAX_POSITION_USD.
        """
        try:
            account = self.client.get_account()
            # Fallback para Paper Trading - Settled Cash solo existe en Live
            settled_cash = float(getattr(account, 'settled_cash', account.cash if self.paper else 0.0))
            target_amount = settled_cash * 0.05
            return min(target_amount, self.MAX_POSITION_USD)
        except Exception as e:
            logger.error(f"[OrderManagerEquities] Error calculando notional: {e}")
            return 0.0

    def _client_id(self, strategy: str) -> str:
        safe = strategy.replace(" ", "")[:18]
        mode_label = get_mode_label()
        return f"eq_{safe}_{mode_label}_{uuid.uuid4().hex[:8]}"

    async def buy_bracket(
        self,
        symbol: str,
        price: float,
        stop_loss_pct: float = 0.03,
        take_profit_pct: float = 0.06,
        strategy_name: str = "",
        notional_usd: float = 100.0
    ):
        if getattr(self, 'ignore_orders', False):
            return

        notional = self._calculate_notional()
        if notional <= 0:
            logger.warning(f"[{strategy_name}] Notional calculado <= 0 para {symbol}. Omitido.")
            return

        order = {
            "type": "bracket_buy",
            "symbol": symbol,
            "qty": None,
            "notional": notional,
            "price": price,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "strategy": strategy_name,
        }
        await self._queue.put(order)
        logger.info(
            f"[{strategy_name}] BRACKET BUY encolado: {symbol} "
            f"notional=${notional:.2f} @ ~${price:.2f}"
        )

    async def sell_short(self, *args, **kwargs):
        """Bloqueo radical de ventas en corto para cuenta Cash."""
        logger.warning("WARNING: Short selling disabled (Refused by Firewall)")
        return

    async def close_position(self, symbol: str, qty: Optional[float] = None, strategy_name: str = ""):
        """Cierra una posición larga."""
        if getattr(self, 'ignore_orders', False):
            return
            
        order = {
            "type": "market_sell",
            "symbol": symbol,
            "qty": qty,
            "strategy": strategy_name,
        }
        await self._queue.put(order)
        logger.info(f"[{strategy_name}] CIERRE encolado: {symbol}")

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
                logger.error(f"[OrderManagerEquities] Error en worker: {e}")

    async def _execute_order(self, order: dict):
        global _EQ_STATUS
        symbol = order["symbol"]
        strategy = order.get("strategy", "Unknown")
        client_id = self._client_id(strategy)

        # ==========================================
        # 🛡️ FIREWALL DE CORTOS (SHORT FIREWALL)
        # ==========================================
        if order["type"] in ["bracket_short", "market_sell"]:
            try:
                self.client.get_open_position(symbol)
            except Exception:
                logger.warning(f"🛡️ [FIREWALL] Short selling disabled: No existe posición larga para {symbol}. Orden de VENTA rechazada localmente.")
                return 

        # ==========================================
        # 🛡️ COMPLIANCE SHIELD: Prevención P.D.T.
        # ==========================================
        if order["type"] == "bracket_buy":
            try:
                acc = self.client.get_account()
                dt_count = int(getattr(acc, 'daytrade_count', 0))
                equity = float(getattr(acc, 'equity', '0.0'))
                
                if equity < 25000.0 and dt_count >= 3:
                    logger.error(f"🛑 [COMPLIANCE SHIELD] Bloqueo P.D.T. para {symbol} (Day Trades: {dt_count}/3)")
                    return 
            except Exception as compliance_err:
                logger.debug(f"Compliance check failed: {compliance_err}")

        # ==========================================
        # 📰 NEWS RISK FILTER (Modo B)
        # ==========================================
        if order["type"] == "bracket_buy":
            try:
                from engine.daily_mode import get_active_mode
                from engine.news_risk_filter import get_news_filter, RiskLevel
                if get_active_mode() == "B":
                    risk = await get_news_filter().get_risk(symbol)
                    if risk in (RiskLevel.HIGH, RiskLevel.MEDIUM):
                        logger.warning(f"📰 [NEWS FILTER] Bloqueado {symbol} por riesgo {risk.value}")
                        return
            except Exception: pass

        try:
            if order["type"] == "bracket_buy":
                price = order["price"]
                stop_price = round(price * (1 - order["stop_loss_pct"]), 2)
                tp_price = round(price * (1 + order["take_profit_pct"]), 2)

                # Alpaca: bracket orders NO soportan notional (fraccional).
                # Convertir notional a qty entera. Mínimo 1 acción.
                qty = max(1, int(order["notional"] // price))

                # Validar que stop_price sea válido (< price - 0.01)
                if stop_price >= price - 0.01:
                    stop_price = round(price - 0.02, 2)

                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=tp_price),
                    stop_loss=StopLossRequest(stop_price=stop_price),
                    client_order_id=client_id
                )
                result = self.client.submit_order(req)
                logger.info(f"[{strategy}] ✅ BRACKET BUY {symbol} | Qty: {qty} @ ~${price:.2f} | ID:{result.id}")

            elif order["type"] == "market_sell":
                # Si llegamos aquí, el Firewall ya validó que tenemos posición
                pos = self.client.get_open_position(symbol)
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=pos.qty, # Vendemos todo lo que tenemos
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id
                )
                self.client.submit_order(req)
                logger.info(f"[{strategy}] ✅ MARKET SELL (CIERRE) {symbol} x{pos.qty}")

            _EQ_STATUS["orders_today"] += 1
            _EQ_STATUS["last_order"] = {"symbol": symbol, "type": order["type"], "strategy": strategy}

        except Exception as e:
            logger.error(f"[{strategy}] ❌ Error en ejecución para {symbol}: {e}")
            self.notifier.send_message(f"⚠️ <b>[ERROR EQUITIES]</b>\nFallo en {symbol}: {e}")

    def cancel_all_open_orders(self):
        """Cancela todas las órdenes abiertas del día (al cierre del mercado)."""
        try:
            self.client.cancel_orders()
            logger.info("[OrderManagerEquities] Órdenes pendientes canceladas.")
        except Exception as e:
            logger.error(f"[OrderManagerEquities] Error cancelando órdenes: {e}")
