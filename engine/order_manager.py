"""
engine/order_manager.py
=======================
Gestor centralizado de órdenes con rate-limiting.

PROBLEMA QUE RESUELVE:
Alpaca permite máximo 200 requests/minuto.
Si 10 estrategias mandan órdenes al mismo tiempo → error 429 "Too Many Requests".

SOLUCIÓN:
Todas las órdenes pasan por aquí y se procesan con un delay mínimo
usando una cola asyncio (sin bloquear otras operaciones).
"""
import asyncio
import logging
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from engine.notifier import TelegramNotifier
import uuid
from alpaca.trading.enums import OrderSide, TimeInForce

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Cola centralizada de órdenes con rate-limiting automático.

    Uso desde una estrategia:
        await self.order_manager.buy("QQQ", qty=10)
        await self.order_manager.sell("QQQ", qty=10)
    """

    # Alpaca Paper Trading: máximo ~200 req/min → 1 orden cada 0.3s es seguro
    MIN_DELAY_SECONDS = 0.3

    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"

        # Cliente REST de Alpaca
        self.client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper
        )

        # Cola de órdenes pendientes
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        
        # Subsistema de alertas
        self.notifier = TelegramNotifier()

        logger.info(f"[OrderManager] Inicializado. Paper={self.paper}")

    async def start(self):
        """Arranca el worker que procesa la cola de órdenes."""
        self._running = True
        logger.info("[OrderManager] Worker de órdenes iniciado.")
        await self._process_queue()

    async def stop(self):
        """Detiene el procesamiento de órdenes."""
        self._running = False
        logger.info("[OrderManager] Worker detenido.")

    async def buy(self, symbol: str, qty: float, limit_price: float = None, strategy_name: str = ""):
        """
        Encola una orden de COMPRA.

        Args:
            symbol: Símbolo del activo (ej: "QQQ")
            qty: Cantidad de acciones
            limit_price: Si se especifica, orden límite. Si no, orden de mercado.
            strategy_name: Nombre de la estrategia (para logs)
        """
        order = {
            "side": "buy",
            "symbol": symbol,
            "qty": qty,
            "limit_price": limit_price,
            "strategy": strategy_name
        }
        await self._queue.put(order)
        logger.info(f"[OrderManager] COMPRA encolada: {qty}x {symbol} (de {strategy_name})")

    async def sell(self, symbol: str, qty: float, limit_price: float = None, strategy_name: str = ""):
        """
        Encola una orden de VENTA.
        """
        order = {
            "side": "sell",
            "symbol": symbol,
            "qty": qty,
            "limit_price": limit_price,
            "strategy": strategy_name
        }
        await self._queue.put(order)
        logger.info(f"[OrderManager] VENTA encolada: {qty}x {symbol} (de {strategy_name})")

    async def _process_queue(self):
        """
        Worker interno: procesa órdenes de la cola una a una con delay.
        Corre en un loop infinito hasta que se llame stop().
        """
        while self._running:
            try:
                # Espera máximo 1 segundo por una nueva orden
                order = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._execute_order(order)
                # Rate limiting: esperar antes de la siguiente orden
                await asyncio.sleep(self.MIN_DELAY_SECONDS)
                self._queue.task_done()
            except asyncio.TimeoutError:
                # No hay órdenes pendientes, continuar el loop
                continue
            except Exception as e:
                logger.error(f"[OrderManager] Error procesando orden: {e}")

    async def _execute_order(self, order: dict):
        """Envía la orden real a la API de Alpaca."""
        symbol = order["symbol"]
        qty = order["qty"]
        side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL
        strategy = order.get("strategy", "Unknown")

        # Crear un ID único para rastrear qué estrategia emitió la orden
        # Max longitud 48 caracteres. Quitamos espacios del nombre.
        safe_strat_name = strategy.replace(" ", "")[:30]
        client_id = f"strat_{safe_strat_name}_{uuid.uuid4().hex[:8]}"

        try:
            if order["limit_price"]:
                # Orden límite
                request = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=order["limit_price"],
                    client_order_id=client_id
                )
                order_type = f"LIMIT @ ${order['limit_price']}"
            else:
                # Orden de mercado
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id
                )
                order_type = "MARKET"

            result = self.client.submit_order(request)
            logger.info(
                f"[{strategy}] EXEC → {order['side'].upper()} {qty}x {symbol} "
                f"@ {order_type} | ID: {result.id}"
            )
            
            # Alerta de Telegram
            emoji = "🟢" if order["side"] == "buy" else "🔴"
            self.notifier.send_message(
                f"{emoji} <b>[Orden Emitida - {strategy}]</b>\n"
                f"Operación: {order['side'].upper()}\n"
                f"Activo: <b>{qty}x {symbol}</b>\n"
                f"Tipo: {order_type}"
            )

        except Exception as e:
            logger.error(f"[{strategy}] ERROR al enviar orden {symbol}: {e}")
            self.notifier.send_message(f"⚠️ <b>[ERROR {strategy}]</b>\nFallo al enviar orden por {symbol}: {e}")

    def get_account(self) -> dict:
        """Retorna info de la cuenta (capital, PnL, etc.) para el dashboard."""
        try:
            account = self.client.get_account()
            return {
                "portfolio_value": float(account.portfolio_value),
                "cash": float(account.cash),
                "equity": float(account.equity),
                "status": account.status.value,
            }
        except Exception as e:
            logger.error(f"[OrderManager] Error obteniendo cuenta: {e}")
            return {}

    def get_positions(self) -> list:
        """Retorna posiciones abiertas para el dashboard."""
        try:
            positions = self.client.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "unrealized_pl": float(p.unrealized_pl),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"[OrderManager] Error obteniendo posiciones: {e}")
            return []
