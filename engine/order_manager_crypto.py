import asyncio
import logging
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from engine.notifier import TelegramNotifier
from typing import Optional
import uuid
try:
    from engine.daily_mode import get_mode_label
except ImportError:
    def get_mode_label(): return "mA"

logger = logging.getLogger(__name__)

class OrderManagerCrypto:
    """
    Gestor centralizado de órdenes exclusivo para Criptomonedas (V1Beta3 API).
    Soporta matemáticas fraccionarias puras (Spot trading).
    """
    MIN_DELAY_SECONDS = 0.4

    def __init__(self, arbiter=None):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        self.paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"

        # Cliente REST de Alpaca
        self.client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper
        )

        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self.notifier = TelegramNotifier()
        
        # Árbitro centralizado de activos (inyectado desde main_crypto)
        self.arbiter: Optional[object] = arbiter

        logger.info(f"[OrderManagerCrypto] Inicializado.")

    async def start(self):
        self._running = True
        logger.info("[OrderManagerCrypto] Worker de órdenes iniciado.")
        await self._process_queue()

    async def stop(self):
        self._running = False
        logger.info("[OrderManagerCrypto] Worker detenido.")

    def _calculate_crypto_qty(self, notional_usd: float, current_price: float, precision: int = 4) -> float:
        """
        Calcula la cantidad fraccionaria exacta permitida para Cripto.
        Ej: Si notional = 1000 y Price = 65000 -> qty = 0.0153
        """
        if current_price <= 0: return 0.0
        exact_qty = notional_usd / current_price
        # Redondear hacia abajo para no exceder fondos (o según doc precision: 4 to 8)
        return round(exact_qty, precision)

    async def request_buy(self, symbol: str, priority: int, strategy_name: str) -> bool:
        """
        Solicita permiso al árbitro antes de comprar.
        Retorna True si está permitido ejecutar una orden de compra.
        """
        if self.arbiter:
            return await self.arbiter.request_buy(symbol, priority, strategy_name)
        return True  # Sin árbitro, siempre permitido (modo legacy)

    def release_asset(self, symbol: str, strategy_name: str) -> None:
        """Notifica al árbitro que la estrategia cerró su posición."""
        if self.arbiter:
            self.arbiter.release(symbol, strategy_name)

    async def buy(self, symbol: str, notional_usd: float, current_price: float, limit_price: float = None, strategy_name: str = "", precision: int = 4):
        qty = self._calculate_crypto_qty(notional_usd, current_price, precision)
        if qty <= 0: return

        order = {
            "side": "buy",
            "symbol": symbol,
            "qty": qty,
            "limit_price": limit_price,
            "strategy": strategy_name
        }
        await self._queue.put(order)
        logger.info(f"[{strategy_name}] COMPRA CRIPTO encolada: {qty} {symbol} (${notional_usd})")

    async def sell_exact(self, symbol: str, exact_qty: float, limit_price: float = None, strategy_name: str = ""):
        """
        Vende una cantidad exacta que ya posees en el balance.
        """
        if exact_qty <= 0: return

        order = {
            "side": "sell",
            "symbol": symbol,
            "qty": exact_qty,
            "limit_price": limit_price,
            "strategy": strategy_name
        }
        await self._queue.put(order)
        logger.info(f"[{strategy_name}] VENTA CRIPTO encolada: {exact_qty} {symbol}")

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
                logger.error(f"[OrderManagerCrypto] Error procesando orden: {e}")

    async def _execute_order(self, order: dict):
        symbol = order["symbol"]
        qty = order["qty"]
        side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL
        strategy = order.get("strategy", "Unknown")

        safe_strat_name = strategy.replace(" ", "")[:24]
        mode_label = get_mode_label()  # → 'mA', 'mB' o 'mC'
        client_id = f"cry_{safe_strat_name}_{mode_label}_{uuid.uuid4().hex[:8]}"

        try:
            if order["limit_price"]:
                request = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.GTC, # Crypto is 24/7, GTC is standard
                    limit_price=order["limit_price"],
                    client_order_id=client_id
                )
                order_type = f"LIMIT @ ${order['limit_price']}"
            else:
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.GTC,
                    client_order_id=client_id
                )
                order_type = "MARKET"

            result = self.client.submit_order(request)
            logger.info(
                f"[{strategy}] EXEC CRIPTO → {order['side'].upper()} {qty}x {symbol} "
                f"@ {order_type} | ID: {result.id}"
            )
            
        except Exception as e:
            logger.error(f"[{strategy}] ERROR al enviar orden {symbol}: {e}")
            self.notifier.send_message(f"⚠️ <b>[ERROR CRIPTO {strategy}]</b>\nFallo al enviar orden por {symbol}: {e}")
