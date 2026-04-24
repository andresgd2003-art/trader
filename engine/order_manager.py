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
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from engine.notifier import TelegramNotifier


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
        if getattr(self, 'ignore_orders', False):
            return
        order = {"side": "buy", "symbol": symbol, "strategy": strategy_name}
        await self._queue.put(order)
        logger.info(f"[OrderManager] COMPRA encolada para {symbol} ({strategy_name})")

    async def sell(self, symbol: str, strategy_name: str = ""):
        """
        Encola una orden de VENTA para liquidar la posición completa.
        """
        if getattr(self, 'ignore_orders', False):
            return
        order = {"side": "sell", "symbol": symbol, "strategy": strategy_name}
        await self._queue.put(order)
        logger.info(f"[OrderManager] VENTA encolada para {symbol} ({strategy_name})")

    async def sell_exact(self, symbol: str, exact_qty: float, strategy_name: str = ""):
        """
        Encola una orden de VENTA para liquidar una cantidad exacta (fraccionaria permitida).
        """
        if getattr(self, 'ignore_orders', False):
            return
        if exact_qty <= 0: return
        order = {"side": "sell", "symbol": symbol, "qty": exact_qty, "strategy": strategy_name}
        await self._queue.put(order)
        logger.info(f"[OrderManager] VENTA EXACTA encolada para {exact_qty} {symbol} ({strategy_name})")

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

    async def _cancel_opposite_open_orders(self, symbol: str, intended_side: OrderSide, strategy: str) -> bool:
        """
        Previene 'potential wash trade' de Alpaca: si existe una orden ABIERTA
        del lado opuesto para `symbol`, la cancela y espera (poll corto) a que
        quede cancelada. Devuelve True si es seguro proceder.
        """
        try:
            opposite = OrderSide.SELL if intended_side == OrderSide.BUY else OrderSide.BUY
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
            open_orders = self.client.get_orders(filter=req) or []
            opposite_orders = [o for o in open_orders if getattr(o, 'side', None) == opposite]

            if not opposite_orders:
                return True

            for o in opposite_orders:
                try:
                    logger.warning(f"[{strategy}] Wash-trade guard: cancelando {opposite} pendiente {o.id} en {symbol} antes de {intended_side}")
                    self.client.cancel_order_by_id(o.id)
                except Exception as e:
                    logger.error(f"[{strategy}] No se pudo cancelar orden opuesta {o.id} en {symbol}: {e}. Abortando {intended_side}.")
                    return False

            # Poll corto (hasta ~2s) hasta confirmar que ya no hay opuestas abiertas
            for _ in range(10):
                await asyncio.sleep(0.2)
                try:
                    still = self.client.get_orders(filter=req) or []
                    if not any(getattr(o, 'side', None) == opposite for o in still):
                        return True
                except Exception:
                    continue
            logger.warning(f"[{strategy}] Orden opuesta aún abierta tras cancelar en {symbol}. Abortando {intended_side} por seguridad.")
            return False
        except Exception as e:
            logger.error(f"[{strategy}] Error en wash-trade guard para {symbol}: {e}. Abortando {intended_side}.")
            return False

    async def _execute_order(self, order: dict):
        symbol = order["symbol"]
        strategy = order.get("strategy", "Unknown")
        side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL

        try:
            # 1. Obtener Settled Cash para el cálculo
            account = self.client.get_account()
            # En Paper Trading no existe settled_cash, usamos cash como fallback
            settled_cash = float(getattr(account, 'settled_cash', account.cash if self.paper else 0.0))
            
            # 2. Lógica de Venta (Liquidar todo o exacto)
            if side == OrderSide.SELL:
                qty_to_sell = order.get("qty")
                try:
                    pos = self.client.get_open_position(symbol)
                    available_qty = float(pos.qty)
                    if available_qty <= 0:
                        logger.warning(f"[{strategy}] Sin qty disponible para vender {symbol}. Ignorado.")
                        return
                    if qty_to_sell is not None:
                        qty_to_sell = min(qty_to_sell, available_qty)
                    else:
                        qty_to_sell = available_qty
                except Exception:
                    # Posición no existe — probablemente ya cerrada por bracket/stop de Alpaca
                    logger.warning(f"[{strategy}] VENTA ignorada: {symbol} no tiene posición abierta (ya cerrada por Alpaca).")
                    return

                # Wash-trade guard: cancelar BUY pendiente opuesta si existe
                if not await self._cancel_opposite_open_orders(symbol, OrderSide.SELL, strategy):
                    return

                try:
                    client_id = f"strat_{strategy.replace(' ','')}_{uuid.uuid4().hex[:8]}"
                    req = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty_to_sell,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                        client_order_id=client_id
                    )
                    self.client.submit_order(req)
                    logger.info(f"[{strategy}] ✅ VENTA ejecutada: {qty_to_sell} {symbol}")
                except Exception as e:
                    logger.error(f"[{strategy}] Error enviando SELL {symbol}: {e}")
                return

            # 3. Lógica de Compra (Sizing Dinámico por Régimen)
            from engine.regime_manager import get_current_regime
            
            regime_data  = get_current_regime()
            regime_str   = regime_data.get("regime", "UNKNOWN")
            
            # Escala de riesgo por régimen
            REGIME_NOTIONAL_PCT = {
                "BULL":    0.08,   # 8% — agresividad aumentada ($40)
                "CHOP":    0.05,   # 5% — moderado ($25)
                "BEAR":    0.03,   # 3% — conservador ($15)
                "UNKNOWN": 0.03,   # 3% — seguro por defecto
            }
            pct = REGIME_NOTIONAL_PCT.get(regime_str, 0.02)
            
            dynamic_notional = round(settled_cash * pct, 2)
            logger.info(f"[OrderManager] Sizing régimen {regime_str}: {pct*100:.0f}% → ${dynamic_notional}")

            if dynamic_notional < 1.0:
                logger.warning(f"[{strategy}] Fondos insuficientes para {symbol} (Calc: ${dynamic_notional})")
                return

            # Wash-trade guard: cancelar SELL pendiente opuesta si existe
            if not await self._cancel_opposite_open_orders(symbol, OrderSide.BUY, strategy):
                return

            # 4. Generar ID único y enviar orden Notional
            client_id = f"strat_{strategy.replace(' ','')}_{uuid.uuid4().hex[:8]}"

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
