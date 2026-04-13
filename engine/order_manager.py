"""
engine/order_manager.py
=======================
Gestor centralizado de órdenes con Sizing Dinámico para Cuenta CASH.

CAMBIOS CLAVE (PROMPT 2):
1. Eliminado parámetro 'qty'.
2. Cálculo automático de notional (4% del settled_cash).
3. Uso estricto de órdenes 'notional' para permitir fracciones en ETFs.
"""
import asyncio
import logging
import os
import uuid
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from engine.notifier import TelegramNotifier

try:
    from engine.daily_mode import get_mode_label
except ImportError:
    def get_mode_label(): return "mA"

logger = logging.getLogger(__name__)

class OrderManager:
    """
    Cola de órdenes con Sizing Dinámico (4% Settled Cash).
    """
    MIN_DELAY_SECONDS = 0.4

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

        logger.info(f"[OrderManager] Dinámico Inicializado. Modo: {'Paper' if self.paper else 'Live'}")

    async def start(self):
        self._running = True
        logger.info("[OrderManager] Worker de Sizing Dinámico iniciado.")
        await self._process_queue()

    async def stop(self):
        self._running = False

    async def buy(self, symbol: str, strategy_name: str = ""):
        """
        Encola una orden de COMPRA. El monto se calcula en la ejecución.
        """
        order = {"side": "buy", "symbol": symbol, "strategy": strategy_name}
        await self._queue.put(order)
        logger.info(f"[OrderManager] COMPRA encolada para {symbol} ({strategy_name})")

    async def sell(self, symbol: str, strategy_name: str = ""):
        """
        Encola una orden de VENTA para liquidar la posición completa.
        """
        order = {"side": "sell", "symbol": symbol, "strategy": strategy_name}
        await self._queue.put(order)
        logger.info(f"[OrderManager] VENTA encolada para {symbol} ({strategy_name})")

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
                logger.error(f"[OrderManager] Error en cola: {e}")

    async def _execute_order(self, order: dict):
        symbol = order["symbol"]
        strategy = order.get("strategy", "Unknown")
        side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL

        try:
            # 1. Obtener Settled Cash para el cálculo
            account = self.client.get_account()
            # En Paper Trading no existe settled_cash, usamos cash como fallback
            settled_cash = float(getattr(account, 'settled_cash', account.cash if self.paper else 0.0))
            
            # 2. Lógica de Venta (Liquidar todo)
            if side == OrderSide.SELL:
                try:
                    # Buscamos la posición actual para cerrar todo
                    pos = self.client.get_open_position(symbol)
                    qty_to_sell = pos.qty
                    req = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty_to_sell,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY
                    )
                    self.client.submit_order(req)
                    logger.info(f"[{strategy}] VENTA ejecutada: Todo el inventario de {symbol}")
                except Exception as e:
                    logger.error(f"[{strategy}] Error al intentar vender {symbol}: {e}")
                return

            # 3. Lógica de Compra (Sizing Dinámico 4%)
            # Cálculo: 4% del cash asentado, redondeado a 2 decimales
            dynamic_notional = round(settled_cash * 0.04, 2)

            if dynamic_notional < 1.0:
                logger.warning(f"[{strategy}] Fondos insuficientes para {symbol} (Calc: ${dynamic_notional})")
                return

            # 4. Generar ID único y enviar orden Notional
            mode_label = get_mode_label()
            client_id = f"etf_{strategy.replace(' ','')[:10]}_{mode_label}_{uuid.uuid4().hex[:6]}"

            request = MarketOrderRequest(
                symbol=symbol,
                notional=dynamic_notional,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                client_order_id=client_id
            )

            result = self.client.submit_order(request)
            logger.info(f"[{strategy}] ✅ COMPRA DINÁMICA: {symbol} por ${dynamic_notional} | ID: {result.id}")

        except Exception as e:
            error_msg = f"[{strategy}] ❌ ERROR CRÍTICO enviando orden {symbol}: {e}"
            logger.error(error_msg)
            self.notifier.send_message(f"⚠️ <b>[ERROR ORDER MANAGER]</b>\n{error_msg}")

    # Helpers para el dashboard
    def get_account(self) -> dict:
        try:
            acc = self.client.get_account()
            return {"portfolio_value": float(acc.portfolio_value), "cash": float(acc.cash), "settled_cash": float(getattr(acc, 'settled_cash', 0.0))}
        except: return {}
