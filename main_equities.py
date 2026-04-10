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
from strategies_equities import (
    GapperMomentumStrategy,
    VCPStrategy,
    GapFadeStrategy,
    PEADStrategy,
    GammaSqueezeStrategy,
    RSIExtremeStrategy,
    StatArbStrategy,
    NLPSentimentStrategy,
    InsiderFlowStrategy,
    SectorRotationStrategy,
)

logger = logging.getLogger("EquitiesEngine")

API_KEY    = os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")

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

        # Portfolio Manager (circuit breaker)
        self.portfolio_manager = PortfolioManager(
            order_manager=self.order_manager,
            strategies=self.strategies
        )

    def _register_strategies(self) -> list:
        strats = [
            GapperMomentumStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            VCPStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            GapFadeStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            PEADStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            GammaSqueezeStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            RSIExtremeStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            StatArbStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            NLPSentimentStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            InsiderFlowStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
            SectorRotationStrategy(order_manager=self.order_manager, regime_manager=self.regime_manager),
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
        strat_gapper = self.strategies[0]
        strat_fade   = self.strategies[2]
        strat_rsi    = self.strategies[5]

        if hasattr(strat_gapper, 'update_symbols'):
            strat_gapper.update_symbols(gainers)
        if hasattr(strat_fade, 'update_symbols'):
            strat_fade.update_symbols(gainers)
        if hasattr(strat_rsi, 'update_symbols'):
            strat_rsi.update_symbols(losers)

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

    async def _on_news(self, news):
        """Handler de noticias — alimenta la estrategia NLP."""
        nlp_strat: NLPSentimentStrategy = self.strategies[7]
        if hasattr(news, 'symbols') and news.symbols:
            for sym in news.symbols:
                headline = getattr(news, 'headline', '') or getattr(news, 'summary', '')
                nlp_strat.on_news(sym, headline)

    async def _portfolio_monitor(self):
        """Chequea el portfolio cada 5 minutos."""
        while True:
            await asyncio.sleep(300)
            self.portfolio_manager.check()

    async def _insider_cron(self):
        """Cron job a las 18:00 EST para buscar Form 4 de EDGAR."""
        insider_strat: InsiderFlowStrategy = self.strategies[8]
        while True:
            now = datetime.now()
            # Esperar hasta las 18:00 EST
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
            if now >= target:
                target = target.replace(day=target.day + 1)
            wait_secs = (target - now).total_seconds()
            await asyncio.sleep(wait_secs)
            logger.info("[EquitiesEngine] Ejecutando cron de Insider Filings (18:00 EST)...")
            await insider_strat.fetch_insider_filings()

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
        return list(set(
            self._eq_symbols
            + self.strategies[1].symbols   # VCP High Beta
            + self.strategies[4].symbols   # Gamma Squeeze candidates
            + self.strategies[6].symbols   # Stat Arb pairs
            + self.strategies[9].symbols   # Sector ETFs + holdings
        ))

    async def start_engine(self):
        """Punto de entrada principal del motor de equities."""
        _EQ_ENGINE_STATUS["is_running"] = True

        # Inicio del order manager
        order_task = asyncio.create_task(self.order_manager.start())

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

        _EQ_ENGINE_STATUS["market_open"] = True
        logger.info(
            f"[EquitiesEngine] ✅ Listo. {len(self.get_eq_symbols())} símbolos activos. "
            f"El stream es compartido con el ETF engine (evita límite IEX)."
        )

        # Lanzar tareas concurrentes (sin stream propio)
        await asyncio.gather(
            self._portfolio_monitor(),
            self._insider_cron(),
            self._market_close_routine(),
            return_exceptions=True
        )
