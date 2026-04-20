"""
main.py — AlpacaNode Trading Engine
=====================================
Punto de entrada principal del sistema.

ARQUITECTURA: "Central Dispatcher"
- UNA sola conexión WebSocket a Alpaca (eficiente)
- Los datos de mercado se distribuyen a las 10 estrategias registradas
- Un OrderManager centralizado maneja todas las órdenes con rate-limiting
- Un API Server FastAPI expone datos al dashboard en tiempo real
"""
import os
import time

# Forzar Huso Horario al Mercado Bursátil Americano (America/New_York)
# Esto previene desfases sin importar dónde corra físicamente el Servidor/VPS (Zero-Risk Live Money).
os.environ['TZ'] = 'America/New_York'
if hasattr(time, 'tzset'):
    time.tzset()

import asyncio
import logging
import threading
import signal
from datetime import datetime
from dotenv import load_dotenv
import os

# Cargar variables de entorno desde .env de forma ABSOLUTA
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

# Configurar el logger ANTES de importar nada más
from engine.logger import setup_logger
# [P4 FIX - 2026-04-15] Mapeo explícito a /opt/trader/data para prevenir Split-Brain
setup_logger(log_path=os.environ.get("LOG_PATH", "/opt/trader/data/engine.log"))

logger = logging.getLogger("Engine")

from engine.notifier import TelegramNotifier
notifier = TelegramNotifier()

# Iniciar el API server en background (Dashboard)
from api_server import start_api_server
_api_thread = threading.Thread(
    target=start_api_server,
    kwargs={"host": "0.0.0.0", "port": 8000},
    daemon=True,
    name="api-server"
)
_api_thread.start()
logger.info("[Engine] API Dashboard arrancado en http://0.0.0.0:8000")

from alpaca.data.live import StockDataStream
from alpaca.data.live.news import NewsDataStream
from alpaca.data.enums import DataFeed

from engine.order_manager import OrderManager
from engine.regime_manager import RegimeManager
from strategies import (
    GoldenCrossStrategy,
    DonchianBreakoutStrategy,
    MomentumRotationStrategy,
    MACDTrendStrategy,
    RSIDipStrategy,
    BollingerReversionStrategy,
    VIXFilteredReversionStrategy,
    VWAPBounceStrategy,
    PairsTradingStrategy,
    GridTradingStrategy,
)

# ============================================================
# CONFIGURACIÓN GLOBAL
# ============================================================
API_KEY    = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
PAPER      = True if API_KEY and API_KEY.startswith("PK") else False

# Todos los símbolos que necesitamos recibir en el WebSocket
ALL_SYMBOLS = ["QQQ", "SMH", "PSQ", "SRVR", "SPY", "SOXX", "TQQQ", "XLC", "IWM", "DIA"]


# ============================================================
# DISPATCHER CENTRAL
# ============================================================
class TradingEngine:
    """
    Motor central: recibe datos de mercado y los distribuye a las estrategias.
    """

    def __init__(self):
        logger.info("=" * 50)
        logger.info("AlpacaNode Trading Engine arrancando...")
        logger.info(f"Modo: {'PAPER TRADING' if PAPER else '⚠️ LIVE TRADING'}")
        logger.info("=" * 50)
        
        notifier.send_message(f"🚀 <b>AlpacaNode Trading Engine</b> arrancando...\nModo: {'PAPER TRADING' if PAPER else 'LIVE TRADING'}")

        # Gestor de órdenes compartido por todas las estrategias
        self.order_manager = OrderManager()

        # Árbitro de régimen de mercado (Propuesta A — market-environment-analysis skill)
        self.regime_manager = RegimeManager()
        try:
            self.regime_manager.assess()  # Evaluación inicial
        except Exception as e:
            logger.warning(f"[Engine] Evaluación de régimen inicial fallida: {e}")

        # Registro de estrategias
        self.strategies = self._register_strategies()

        # Referencia al EquitiesEngine (se inyecta desde main)
        self.equities_engine = None

        # Stream WebSocket de Alpaca
        self.stream = StockDataStream(
            api_key=API_KEY,
            secret_key=SECRET_KEY,
            feed=DataFeed.IEX   # Feed gratuito, suficiente para paper trading
        )
        
        # Stream WebSocket de Noticias para Estrategia NLP y Risk Filter
        self.news_stream = NewsDataStream(
            api_key=API_KEY,
            secret_key=SECRET_KEY
        )

    def _register_strategies(self) -> list:
        """
        Instancia y registra las 10 estrategias ETF.
        Cada una recibe una referencia al order_manager y al regime_manager.
        """
        rm = self.regime_manager
        strategies = [
            GoldenCrossStrategy(order_manager=self.order_manager, regime_manager=rm),
            DonchianBreakoutStrategy(order_manager=self.order_manager, regime_manager=rm),
            MomentumRotationStrategy(order_manager=self.order_manager, regime_manager=rm),
            MACDTrendStrategy(order_manager=self.order_manager, regime_manager=rm),
            RSIDipStrategy(order_manager=self.order_manager, regime_manager=rm),
            BollingerReversionStrategy(order_manager=self.order_manager, regime_manager=rm),
            VIXFilteredReversionStrategy(order_manager=self.order_manager, regime_manager=rm),
            VWAPBounceStrategy(order_manager=self.order_manager, regime_manager=rm),
            PairsTradingStrategy(order_manager=self.order_manager, regime_manager=rm),
            GridTradingStrategy(order_manager=self.order_manager, regime_manager=rm),
        ]
        logger.info(f"[Engine] {len(strategies)} estrategias ETF registradas.")
        return strategies

    async def _on_bar(self, bar):
        """
        Handler de barras (velas de mercado).
        Distribuye el dato a todas las estrategias ETF + equities.
        """
        tasks = []
        for strategy in self.strategies:
            if strategy.should_process(bar.symbol):
                tasks.append(strategy.on_bar(bar))

        # Despachar al Equities Engine si el símbolo le pertenece
        if self.equities_engine and bar.symbol in self.equities_engine.get_eq_symbols():
            tasks.append(self.equities_engine.dispatch_bar(bar))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _on_quote(self, quote):
        """
        Handler de cotizaciones bid/ask en tiempo real.
        """
        tasks = []
        for strategy in self.strategies:
            if strategy.should_process(quote.symbol):
                tasks.append(strategy.on_quote(quote))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _on_news(self, news):
        """
        Handler de noticias en vivo desde Alpaca.
        Se remite directamente al motor de Equities para NLP.
        """
        if self.equities_engine:
            await self.equities_engine.dispatch_news(news)

    def _subscribe(self):
        """Suscribe el stream a todos los símbolos: ETF + Equities."""
        try:
            eq_symbols = self.equities_engine.get_eq_symbols() if self.equities_engine else []
            # Filter out symbols with dots (like BRK.B) - they crash the IEX WebSocket
            eq_symbols = [s for s in eq_symbols if '.' not in s]
            all_symbols = list(set(ALL_SYMBOLS + eq_symbols))
            
            logger.info(f"[Engine] Preparando suscripción WebSocket IEX para {len(all_symbols)} símbolos...")
            self.stream.subscribe_bars(self._on_bar, *all_symbols)
            logger.info(f"[Engine] ✅ subscribe_bars OK")
            self.stream.subscribe_quotes(self._on_quote, *ALL_SYMBOLS)
            logger.info(f"[Engine] ✅ subscribe_quotes OK")
            
            logger.info(f"[Engine] Suscrito a ETF: {ALL_SYMBOLS}")
            if eq_symbols:
                logger.info(f"[Engine] + Equities symbols: {len(eq_symbols)} adicionales y +News Stream Activo")
        except Exception as e:
            logger.critical(f"[Engine] ❌ ERROR FATAL en _subscribe: {e}")
            import traceback
            logger.critical(traceback.format_exc())

    async def run(self):
        """Arranca el engine completo."""
        
        eq_symbols = self.equities_engine.get_eq_symbols() if self.equities_engine else []
        all_symbols = list(set(ALL_SYMBOLS + eq_symbols))
        
        # --- HISTORICAL PRE-FETCH (Cold Start Fix) ---
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            from datetime import datetime, timedelta

            class PseudoBar:
                def __init__(self, sym, b):
                    self.symbol = sym
                    self.close = b.close
                    self.high = b.high
                    self.low = b.low
                    self.volume = b.volume
                    self.timestamp = b.timestamp

            if all_symbols:
                api_k = os.environ.get("ALPACA_API_KEY", "")
                sec_k = os.environ.get("ALPACA_SECRET_KEY", "")
                hist_client = StockHistoricalDataClient(api_k, sec_k)
                
                from datetime import timezone
                now = datetime.now(timezone.utc)
                req = StockBarsRequest(
                    symbol_or_symbols=all_symbols,
                    timeframe=TimeFrame.Minute,
                    start=now - timedelta(days=5)
                )
                logger.info(f"[Engine] Descargando historial de 5 días para evitar Cold Start...")
                
                # SUPPRESS ORDERS DURING HISTORY
                self.order_manager.ignore_orders = True
                
                bars = hist_client.get_stock_bars(req)
                count = 0
                if hasattr(bars, 'data'):
                    for sym in all_symbols:
                        if sym in bars.data:
                            for b in bars.data[sym]:
                                count += 1
                                pb = PseudoBar(sym, b)
                                await self._on_bar(pb)
                logger.info(f"[Engine] ✅ Historial inyectado: {count} barras de 1Min procesadas.")
                
                # RESTORE AND RE-SYNC STATE
                self.order_manager.ignore_orders = False
                while not self.order_manager._queue.empty():
                    try:
                        self.order_manager._queue.get_nowait()
                        self.order_manager._queue.task_done()
                    except: break

                for strat in self.strategies:
                    for sym in strat.symbols:
                        pos = strat.sync_position_from_alpaca(sym) > 0
                        if hasattr(strat, "_has_position") and isinstance(strat._has_position, dict):
                            strat._has_position[sym] = pos
                        elif hasattr(strat, "in_position"):
                            strat.in_position = pos
                            
        except Exception as e:
            logger.warning(f"[Engine] Falló la pre-carga histórica: {e}")
            self.order_manager.ignore_orders = False
        # ----------------------------------------------

        self._subscribe()

        # Iniciar procesamiento de órdenes en segundo plano
        order_task = asyncio.create_task(self.order_manager.start())
        from engine.daily_reporter import run_daily_summary_loop
        # Lanzar verificador diario
        daily_reporter_task = asyncio.create_task(run_daily_summary_loop())

        # ── NUEVO: Re-evaluación horaria del régimen de mercado (Propuesta A)
        async def hourly_regime_task():
            while True:
                await asyncio.sleep(3600)  # cada hora
                try:
                    self.regime_manager.assess_if_needed()
                except Exception as e:
                    logger.warning(f"[Engine] Regime re-assess error: {e}")

        regime_task = asyncio.create_task(hourly_regime_task())
        logger.info("[Engine] Evaluación horaria de régimen activada.")

        logger.info("[Engine] Conexión WebSocket Alpaca establecida. ¡Engine activo!")

        # Configurar cierre limpio para señales del sistema (Docker stoppping)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        try:
            # stream.run() es bloqueante - inicia el loop de datos en tiempo real
            import threading
            stream_thread = threading.Thread(target=self.stream.run, daemon=True)
            stream_thread.start()
            
            news_thread = threading.Thread(target=self.news_stream.run, daemon=True)
            news_thread.start()
            
            # Mantener el loop de asyncio activo
            while stream_thread.is_alive() or news_thread.is_alive():
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"[Engine] Error en loop principal: {e}")
        finally:
            await self.order_manager.stop()
            order_task.cancel()
            daily_reporter_task.cancel()
            logger.info("[Engine] Engine detenido correctamente.")

    async def stop(self):
        """Detiene el motor de forma limpia, cerrando el WebSocket."""
        logger.info("[Engine] Recibida señal de apagado. Cerrando stream...")
        try:
            # Intentar cerrar el stream de Alpaca
            if hasattr(self.stream, 'stop'):
                 await self.stream.stop()
            elif hasattr(self.stream, 'close'):
                 await self.stream.close()
                 
            if hasattr(self.news_stream, 'stop'):
                 await self.news_stream.stop()
            elif hasattr(self.news_stream, 'close'):
                 await self.news_stream.close()
                 
            logger.info("[Engine] WebSockets cerrados con éxito.")
        except Exception as e:
            logger.error(f"[Engine] Error cerrando WebSockets: {e}")
        
        # Permitir que el proceso termine
        loop = asyncio.get_running_loop()
        loop.stop()


# ============================================================
# PUNTO DE ENTRADA
# ============================================================
if __name__ == "__main__":
    if not API_KEY or not SECRET_KEY:
        logger.error("ERROR: ALPACA_API_KEY y ALPACA_SECRET_KEY son requeridas.")
        logger.error("Configúralas en las variables de entorno o en el archivo .env")
        exit(1)

    # REGLA DE 20 SEGUNDOS: Delay para evitar Error 406 (conlimit) en redeploys rápidos
    logger.info("[Main] Delay de seguridad (20s) activo para liberar sesiones previas en Alpaca...")
    time.sleep(20)

    engine = TradingEngine()
    
    from main_crypto import CryptoTradingEngine
    crypto_engine = CryptoTradingEngine()

    from main_equities import EquitiesEngine
    equities_engine = EquitiesEngine()

    # Inyectar referencia al equities engine para compartir el stream IEX
    engine.equities_engine = equities_engine
    
    def global_exception_handler(loop, context):
        msg = context.get("exception", context.get("message"))
        notifier.send_message_sync(f"🚨 CRITICAL CRASH: {msg}")
        loop.default_exception_handler(context)
    
    async def run_both():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(global_exception_handler)
        
        # Precargar símbolos del universo de equities ANTES de suscribir el WebSocket
        await equities_engine.initialize()
        
        await asyncio.gather(
            engine.run(),
            crypto_engine.start_engine(),
            equities_engine.start_engine(),
            return_exceptions=True
        )

    try:
        asyncio.run(run_both())
    except KeyboardInterrupt:
        logger.info("[Main] Sistema detenido por el usuario.")
