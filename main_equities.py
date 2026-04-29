"""
main_equities.py — AlpacaNode Equities Trading Engine
=======================================================
Motor de trading para acciones individuales de alta volatilidad.

NOTA ARQUITECTURAL IMPORTANTE:
  Alpaca IEX (free tier) solo permite 1 conexión WebSocket simultánea.
  El EquitiesEngine NO crea su propio StockDataStream.
  En cambio, el TradingEngine principal (main.py) suscribe los símbolos
  adicionales al stream existente y llama a equities_engine.dispatch_bar(bar).

FLUJO DIARIO:
  09:00 AM EST → PreMarketScreener.run() → universo dinámico del día
  09:00 AM EST → RegimeManager.assess() → BULL | BEAR | CHOP
  09:30 AM EST → main.py añade símbolos del día al stream compartido
  16:30 PM EST → Cancelar órdenes pendientes
  18:00 PM EST → InsiderFlowStrategy.fetch_insider_filings() (Form 4)
"""
import os
import asyncio
import logging
from datetime import datetime, time as dtime

from engine.screener import PreMarketScreener
from engine.regime_manager import RegimeManager
from engine.order_manager_equities import OrderManagerEquities
from engine.portfolio_manager import PortfolioManager
from engine.notifier import TelegramNotifier
# ⚠️ QUARANTINED 2026-04-24: GammaSqueezeStrategy y SectorRotationStrategy tienen
# lógica de exit rota (18/0 y 28/2 buys/sells respectivamente en últimas 500 órdenes).
# Re-enable solo después de arreglar la lógica de SELL.
from strategies_equities import (
    VCPStrategy,
    DefensiveRotation,
    # GammaSqueezeStrategy,   # QUARANTINED - exits rotos
    # SectorRotationStrategy, # QUARANTINED - exits rotos
)

logger = logging.getLogger("EquitiesEngine")

API_KEY    = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")

# Estado global para el dashboard
_EQ_ENGINE_STATUS: dict = {
    "is_running": False,
    "strategies": [],
    "market_open": False,
}


def get_eq_engine_status() -> dict:
    return _EQ_ENGINE_STATUS


class EquitiesEngine:
    """
    Motor asíncrono para acciones individuales.
    Gestiona el ciclo de vida diario: screener → regime → trading → cierre.
    """

    def __init__(self):
        logger.info("=" * 50)
        logger.info("EquitiesEngine arrancando...")
        logger.info("=" * 50)

        self.screener = PreMarketScreener()
        self.regime_manager = RegimeManager()
        self.order_manager = OrderManagerEquities()

        # Universo dinámico del día
        self.daily_gainers: list = []
        self.daily_losers: list = []
        self._eq_symbols: list = []  # Todos los símbolos a suscribir

        # Instanciar las 10 estrategias
        self.strategies = self._register_strategies()

        # Portfolio Manager (circuit breaker con resume inteligente por régimen)
        self.portfolio_manager = PortfolioManager(
            order_manager=self.order_manager,
            strategies=self.strategies,
            regime_manager=self.regime_manager,
        )

    def _register_strategies(self) -> list:
        strats = [
            VCPStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),          # idx 0, strat 2
            DefensiveRotation(order_manager=self.order_manager, regime_manager=self.regime_manager),
            # ⚠️ QUARANTINED 2026-04-24: exits broken (28/2 and 18/0 buys/sells in last 500 orders).
            # Re-enable solo después de arreglar la lógica de SELL.
            # GammaSqueezeStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager), # idx 1, strat 5
            # SectorRotationStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager), # idx 2, strat 10
        ]
        _EQ_ENGINE_STATUS["strategies"] = [s.name for s in strats]
        logger.info(f"[EquitiesEngine] {len(strats)} estrategias registradas.")
        return strats

    async def _pre_market_routine(self):
        """Rutina de pre-apertura: screener + régimen."""
        logger.info("[EquitiesEngine] Ejecutando rutina pre-mercado (09:00 EST)...")

        # 1. Obtener universo dinámico
        gainers, losers = self.screener.run()
        self.daily_gainers = gainers
        self.daily_losers = losers
        all_symbols = self.screener.get_all_symbols()

        # 2. Actualizar símbolos en estrategias dinámicas
        strat_vcp = self.strategies[0] # VCP es la primera ahora

        if hasattr(strat_vcp, 'update_symbols'):
            strat_vcp.update_symbols(gainers)

        # 3. Evaluar régimen de mercado
        regime = self.regime_manager.assess()
        logger.info(f"[EquitiesEngine] Régimen del día: {regime.value}")

        # 4. Notificar apertura a todas las estrategias
        for s in self.strategies:
            if hasattr(s, 'on_market_open'):
                s.on_market_open()

        return all_symbols

    async def dispatch_bar(self, bar):
        """Punto de entrada público: recibe barras del stream compartido de main.py."""
        await self._on_bar(bar)

    async def _on_bar(self, bar):
        """Handler central de barras — distribuye a todas las estrategias activas."""
        if self.portfolio_manager.is_halted():
            return

        tasks = [
            s.on_bar(bar)
            for s in self.strategies
            if s.should_process(bar.symbol)
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def dispatch_news(self, news):
        """Punto de entrada público: recibe noticias del stream de Alpaca."""
        await self._on_news(news)

    async def _on_news(self, news):
        """Dispatch news to strategies that can process it (PEAD earnings + NLP sentiment)."""
        symbols = getattr(news, 'symbols', []) or []
        headline = getattr(news, 'headline', '') or ''
        for s in self.strategies:
            try:
                if hasattr(s, 'flag_earnings_candidate'):
                    for sym in symbols:
                        s.flag_earnings_candidate(sym)
                if hasattr(s, 'on_news'):
                    for sym in symbols:
                        s.on_news(sym, headline)
            except Exception as e:
                logger.warning(f"[EquitiesEngine] Error in {s.name} news handler: {e}")

    async def _portfolio_monitor(self):
        """Chequea el portfolio cada 5 minutos."""
        while True:
            await asyncio.sleep(300)
            self.portfolio_manager.check()



    async def _market_close_routine(self):
        """A las 16:30 EST, cancelar órdenes pendientes."""
        while True:
            await asyncio.sleep(60)
            now = datetime.now()
            if now.hour == 16 and now.minute >= 30 and _EQ_ENGINE_STATUS.get("market_open"):
                logger.info("[EquitiesEngine] CIERRE DE MERCADO 16:30. Cancelando órdenes.")
                self.order_manager.cancel_all_open_orders()
                _EQ_ENGINE_STATUS["market_open"] = False

    def get_eq_symbols(self) -> list:
        """Retorna todos los símbolos que el equities engine necesita en el stream."""
        all_syms = list(self._eq_symbols)
        for s in self.strategies:
            all_syms.extend(s.symbols)
        return list(set(all_syms))

    async def initialize(self):
        """Inicializa los símbolos obtenidos del screener antes de arrancar los loops infinitos."""
        # Esperar la hora del pre-mercado (09:00 AM EST) o ejecutar ahora
        now = datetime.now()
        pre_market_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now < pre_market_time:
            wait_secs = (pre_market_time - now).total_seconds()
            logger.info(f"[EquitiesEngine] Esperando hasta las 09:00 EST ({wait_secs:.0f}s)...")
            await asyncio.sleep(wait_secs)

        # Rutina pre-mercado (screener + regime)
        symbols_today = await self._pre_market_routine()
        self._eq_symbols = symbols_today

        if not symbols_today:
            logger.warning("[EquitiesEngine] Sin universo hoy. Solo estrategias estáticas activas.")
            
        logger.info(
            f"[EquitiesEngine] ✅ Initialize completado. {len(self.get_eq_symbols())} símbolos listos."
        )

    async def _adopt_orphan_positions(self):
        """
        Al arrancar, detecta posiciones equities abiertas sin stop activo y les asigna
        un trailing stop del 15%. Posiciones bajo $1.00 se liquidan directamente.
        Previene que posiciones de sesiones anteriores queden sin gestión.
        """
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.trading.requests import MarketOrderRequest, TrailingStopOrderRequest, GetOrdersRequest
            from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

            paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"
            client = TradingClient(api_key=API_KEY, secret_key=SECRET_KEY, paper=paper)

            etf_whitelist = {
                "SPY", "QQQ", "TQQQ", "IWM", "DIA", "SMH", "SOXX", "SRVR",
                "XLK", "XLF", "XLV", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC", "XLP", "XLY",
            }

            positions = client.get_all_positions()

            # Cerrar inmediatamente cualquier SHORT en ETF whitelist (huérfano de Pairs Trading)
            for p in positions:
                qty = float(p.qty)
                if p.symbol in etf_whitelist and qty < 0:
                    try:
                        client.close_position(p.symbol)
                        logger.warning(f"[EquitiesEngine] 🔴 SHORT huérfano en ETF cerrado: {p.symbol} qty={qty}")
                        self.notifier.send_message(f"⚠️ <b>[ADOPT]</b> SHORT huérfano cerrado: {p.symbol} qty={qty}")
                    except Exception as e:
                        logger.error(f"[EquitiesEngine] Error cerrando SHORT huérfano {p.symbol}: {e}")

            eq_positions = [
                p for p in positions
                if p.asset_class.value != 'crypto'
                and '/' not in p.symbol
                and p.symbol not in etf_whitelist
            ]

            if not eq_positions:
                logger.info("[EquitiesEngine] Sin posiciones equities huérfanas al arrancar.")
                return

            # Obtener símbolos con órdenes stop/bracket activas
            open_orders = client.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, limit=200
            ))
            protected_symbols = set()
            for o in open_orders:
                order_type = str(getattr(o, 'order_type', '') or '').lower()
                order_class = str(getattr(o, 'order_class', '') or '').lower()
                if any(t in order_type or t in order_class for t in ['stop', 'bracket', 'trailing']):
                    protected_symbols.add(o.symbol)

            adopted, liquidated = [], []

            for pos in eq_positions:
                sym = pos.symbol
                qty = float(pos.qty)
                price = float(pos.current_price) if pos.current_price else 0.0
                # qty retenida por órdenes abiertas (bracket SL/TP, etc.)
                held = float(getattr(pos, 'held_for_orders', 0) or 0)
                qty_available = float(getattr(pos, 'qty_available', qty - held) or 0)
                is_fractional = (qty != int(qty))

                if qty <= 0 or sym in protected_symbols:
                    continue

                if price < 1.00:
                    # Liquidar penny stocks directamente
                    try:
                        # Si no hay qty disponible pero sí hay held, cancelar órdenes abiertas del símbolo
                        if qty_available <= 0 and held > 0:
                            try:
                                sym_open_orders = client.get_orders(filter=GetOrdersRequest(
                                    status=QueryOrderStatus.OPEN, symbols=[sym], limit=50
                                ))
                                for oo in sym_open_orders:
                                    try:
                                        client.cancel_order_by_id(oo.id)
                                    except Exception:
                                        pass
                                await asyncio.sleep(1)
                                pos = client.get_open_position(sym)
                                qty = float(pos.qty)
                                qty_available = float(getattr(pos, 'qty_available', qty) or 0)
                                is_fractional = (qty != int(qty))
                            except Exception as cancel_err:
                                logger.warning(f"[EquitiesEngine] Error cancelando órdenes de {sym}: {cancel_err}")

                        sell_qty = qty_available if qty_available > 0 else qty
                        if sell_qty <= 0:
                            logger.warning(f"[EquitiesEngine] {sym}: sin qty disponible para liquidar. Skip.")
                            continue

                        req = MarketOrderRequest(
                            symbol=sym,
                            qty=sell_qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY,
                            client_order_id=self.order_manager._client_id("Adopt_Liquidate"),
                        )
                        client.submit_order(req)
                        liquidated.append(f"{sym}(${price:.2f})")
                        logger.warning(f"[EquitiesEngine] 🗑️ LIQUIDADA posición huérfana (precio<$1): {sym} qty={sell_qty}")
                    except Exception as e:
                        logger.error(f"[EquitiesEngine] Error liquidando {sym}: {e}")
                else:
                    # Para fraccionales, Alpaca NO acepta trailing_stop; usar MARKET DAY para liquidar.
                    if is_fractional:
                        try:
                            req = MarketOrderRequest(
                                symbol=sym,
                                qty=qty,
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.DAY,
                                client_order_id=self.order_manager._client_id("Adopt_FracLiq"),
                            )
                            client.submit_order(req)
                            liquidated.append(f"{sym}(${price:.2f} frac)")
                            logger.warning(f"[EquitiesEngine] 🗑️ LIQUIDADA fraccional huérfana: {sym} qty={qty} @ ${price:.2f} (trailing no soportado en fraccionales)")
                        except Exception as e:
                            logger.error(f"[EquitiesEngine] Error liquidando fraccional {sym}: {e}")
                        continue
                    # Colocar trailing stop 15% (solo qty entera)
                    try:
                        req = TrailingStopOrderRequest(
                            symbol=sym,
                            qty=qty,
                            side=OrderSide.SELL,
                            time_in_force=TimeInForce.DAY,
                            trail_percent=15.0,
                            client_order_id=self.order_manager._client_id("Adopt_Trail"),
                        )
                        client.submit_order(req)
                        adopted.append(f"{sym}(${price:.2f})")
                        logger.info(f"[EquitiesEngine] 🛡️ ADOPTADA posición huérfana: {sym} qty={qty} @ ${price:.2f} → trailing 15%")
                    except Exception as e:
                        logger.error(f"[EquitiesEngine] Error adoptando {sym}: {e}")

            if adopted or liquidated:
                msg = "🛡️ <b>[ADOPCIÓN DE POSICIONES]</b>\n"
                if adopted:
                    msg += f"Trailing stop 15% → {', '.join(adopted)}\n"
                if liquidated:
                    msg += f"Liquidadas (precio &lt;$1) → {', '.join(liquidated)}"
                TelegramNotifier().send_message(msg)

        except Exception as e:
            logger.error(f"[EquitiesEngine] Error en adopción de posiciones huérfanas: {e}")

    async def start_engine(self):
        """Punto de entrada principal con resiliencia global."""
        _EQ_ENGINE_STATUS["is_running"] = True
        _EQ_ENGINE_STATUS["market_open"] = True

        # Inicio del order manager
        order_task = asyncio.create_task(self.order_manager.start())

        # Adoptar posiciones huérfanas de sesiones anteriores
        await self._adopt_orphan_positions()

        try:
            # Lanzar tareas concurrentes
            await asyncio.gather(
                self._portfolio_monitor(),
                self._market_close_routine(),
                return_exceptions=False # Queremos que el try/except maneje errores graves
            )
        except Exception as e:
            logger.critical(f"[EquitiesEngine] FALLO GLOBAL DEL MOTOR: {e}")
            from engine.notifier import TelegramNotifier
            TelegramNotifier().send_message(f"🚨 <b>[EQUITIES CRITICAL]</b>\nFallo global del motor de acciones: {e}")
        finally:
            _EQ_ENGINE_STATUS["is_running"] = False
