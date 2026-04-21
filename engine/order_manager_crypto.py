import asyncio
import logging
import os
from datetime import datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from engine.notifier import TelegramNotifier
from typing import Optional
import uuid

logger = logging.getLogger(__name__)

# Horario de mercado US (America/New_York — TZ ya está seteado en main.py)
_MARKET_OPEN_HOUR  = 9
_MARKET_OPEN_MIN   = 30
_MARKET_CLOSE_HOUR = 16
_MARKET_CLOSE_MIN  = 30

def _us_market_is_open() -> bool:
    """Retorna True si el mercado de valores US está abierto ahora mismo."""
    now = datetime.now()
    if now.weekday() >= 5:          # Sábado=5, Domingo=6
        return False
    open_mins  = _MARKET_OPEN_HOUR  * 60 + _MARKET_OPEN_MIN
    close_mins = _MARKET_CLOSE_HOUR * 60 + _MARKET_CLOSE_MIN
    now_mins   = now.hour * 60 + now.minute
    return open_mins <= now_mins < close_mins


class OrderManagerCrypto:
    """
    Gestor centralizado de órdenes exclusivo para Criptomonedas (V1Beta3 API).
    Soporta matemáticas fraccionarias puras (Spot trading).

    Capital dinámico:
      - Mercado abierto  → cap $15 (conservador, capital reservado para ETF/Equities)
      - Mercado cerrado  → cap expandido (20% del capital disponible, máx $40)
        con reserva fija del 40% del settled_cash para la apertura del día siguiente.
    """
    MIN_DELAY_SECONDS = 0.4

    # Caps y reservas para el modo noche
    DAY_CAP_USD       = 15.0    # Cap durante horas de mercado
    NIGHT_CAP_MAX_USD = 40.0    # Tope absoluto nocturno
    NIGHT_CAP_PCT     = 0.20    # % del capital disponible nocturno por posición
    NIGHT_RESERVE_PCT = 0.40    # % de settled_cash siempre reservado para apertura
    MIN_EQUITY_EXPAND = 80.0    # No expandir si equity total < $80

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
        self.ignore_orders = False   # Flag de prefetch: bloquea órdenes durante inyección histórica
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

    def _get_dynamic_cap(self) -> float:
        """
        Retorna el cap de notional en USD según el horario de mercado.
        Durante horas de mercado: $15 (conservador).
        Fuera de mercado: hasta 20% del capital disponible (máx $40),
        reservando siempre un 40% del settled_cash para la apertura.
        """
        if _us_market_is_open():
            return self.DAY_CAP_USD

        # Modo noche: calcular cap dinámico
        try:
            account = self.client.get_account()
            equity = float(account.equity or 0)
            settled = float(getattr(account, 'settled_cash', account.cash if self.paper else 0))

            # No expandir si la cuenta está muy baja
            if equity < self.MIN_EQUITY_EXPAND:
                logger.debug(f"[OrderManagerCrypto] Equity ${equity:.2f} < ${self.MIN_EQUITY_EXPAND} → modo conservador nocturno.")
                return self.DAY_CAP_USD

            reserve    = settled * self.NIGHT_RESERVE_PCT
            available  = max(settled - reserve, 0.0)
            night_cap  = min(available * self.NIGHT_CAP_PCT, self.NIGHT_CAP_MAX_USD)

            if night_cap > self.DAY_CAP_USD:
                logger.info(f"[OrderManagerCrypto] 🌙 Modo noche: cap ${night_cap:.2f} (equity ${equity:.2f}, disponible ${available:.2f}, reserva ${reserve:.2f})")
            else:
                logger.info(f"[OrderManagerCrypto] 🌙 Modo noche activo pero cap=${night_cap:.2f} ≤ cap diurno ${self.DAY_CAP_USD} (equity ${equity:.2f} — cuenta pequeña)")
            return max(night_cap, self.DAY_CAP_USD)   # Nunca menos que el cap diurno

        except Exception as e:
            logger.warning(f"[OrderManagerCrypto] Error calculando cap nocturno: {e}. Usando cap diurno.")
            return self.DAY_CAP_USD

    def _calculate_crypto_qty(self, notional_usd: float, current_price: float, precision: int = 4) -> float:
        """
        Calcula la cantidad fraccionaria exacta permitida para Cripto.
        El cap se ajusta dinámicamente según horario de mercado.
        """
        if current_price <= 0: return 0.0
        cap = self._get_dynamic_cap()
        capped_notional = min(notional_usd, cap)
        exact_qty = capped_notional / current_price
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
        if self.ignore_orders:
            return
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
        if self.ignore_orders:
            return
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
        # Última línea de defensa: si ignore_orders sigue True (por un edge case),
        # descartar la orden antes de tocar Alpaca.
        if self.ignore_orders:
            logger.debug(
                f"[OrderManagerCrypto] ignore_orders=True, descartando "
                f"{order.get('side')} {order.get('symbol')} de {order.get('strategy')}"
            )
            return
        symbol = order["symbol"]
        qty = order["qty"]
        side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL
        strategy = order.get("strategy", "Unknown")

        safe_strat_name = strategy.replace(" ", "")[:24]
        client_id = f"cry_{safe_strat_name}_{uuid.uuid4().hex[:8]}"

        try:
            # FIX Redondeo Crypto: Si es VENTA, asegurarse de no pedir más de lo que hay
            if side == OrderSide.SELL:
                # Validación defensiva pre-SELL: abortar si no hay posición real
                try:
                    real_pos = self.client.get_open_position(symbol)
                    real_qty = float(getattr(real_pos, "qty_available", None) or real_pos.qty or 0)
                    if real_qty <= 0:
                        logger.error(f"[{strategy}] [CryptoSELL] Sin posición real en {symbol}, abortando SELL.")
                        return
                    
                    # Si pedimos vender más de lo que hay, o si es una orden market pura que antes usaba close_position
                    if qty > real_qty or not order.get("limit_price"):
                        logger.info(f"[{strategy}] [CryptoSELL] Ajustando qty a {real_qty} para venta completa de {symbol}.")
                        qty = real_qty
                except Exception as pos_e:
                    logger.error(f"[{strategy}] [CryptoSELL] No se pudo verificar posición de {symbol}: {pos_e}. Abortando por seguridad.")
                    return

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
