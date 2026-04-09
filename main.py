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
import asyncio
import os
import logging
import threading
from dotenv import load_dotenv

# Cargar variables de entorno desde .env (si existe localmente)
load_dotenv()

# Configurar el logger ANTES de importar nada más
from engine.logger import setup_logger
setup_logger(log_path=os.environ.get("LOG_PATH", "/app/data/engine.log"))

logger = logging.getLogger("Engine")

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
from alpaca.data.enums import DataFeed

from engine.order_manager import OrderManager
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
API_KEY    = os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
PAPER      = os.environ.get("PAPER_TRADING", "True").lower() == "true"

# Todos los símbolos que necesitamos recibir en el WebSocket
ALL_SYMBOLS = ["QQQ", "SMH", "XLK", "SRVR", "SPY", "SOXX", "TQQQ"]


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

        # Gestor de órdenes compartido por todas las estrategias
        self.order_manager = OrderManager()

        # Registro de estrategias
        self.strategies = self._register_strategies()

        # Stream WebSocket de Alpaca
        self.stream = StockDataStream(
            api_key=API_KEY,
            secret_key=SECRET_KEY,
            feed=DataFeed.IEX   # Feed gratuito, suficiente para paper trading
        )

    def _register_strategies(self) -> list:
        """
        Instancia y registra las 10 estrategias.
        Cada una recibe una referencia al order_manager compartido.
        """
        strategies = [
            GoldenCrossStrategy(order_manager=self.order_manager),
            DonchianBreakoutStrategy(order_manager=self.order_manager),
            MomentumRotationStrategy(order_manager=self.order_manager),
            MACDTrendStrategy(order_manager=self.order_manager),
            RSIDipStrategy(order_manager=self.order_manager),
            BollingerReversionStrategy(order_manager=self.order_manager),
            VIXFilteredReversionStrategy(order_manager=self.order_manager),
            VWAPBounceStrategy(order_manager=self.order_manager),
            PairsTradingStrategy(order_manager=self.order_manager),
            GridTradingStrategy(order_manager=self.order_manager),
        ]
        logger.info(f"[Engine] {len(strategies)} estrategias registradas.")
        return strategies

    async def _on_bar(self, bar):
        """
        Handler de barras (velas de mercado).
        Se llama con CADA barra que llega del stream.
        Distribuye el dato a todas las estrategias que monitorean ese símbolo.
        """
        tasks = []
        for strategy in self.strategies:
            if strategy.should_process(bar.symbol):
                tasks.append(strategy.on_bar(bar))

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

    def _subscribe(self):
        """Suscribe el stream a todos los símbolos y tipos de datos necesarios."""
        # Barras por minuto (1-minute bars)
        self.stream.subscribe_bars(self._on_bar, *ALL_SYMBOLS)
        # Cotizaciones en tiempo real
        self.stream.subscribe_quotes(self._on_quote, *ALL_SYMBOLS)
        logger.info(f"[Engine] Suscrito a: {ALL_SYMBOLS}")

    async def run(self):
        """Arranca el engine completo."""
        self._subscribe()

        # Iniciar procesamiento de órdenes en segundo plano
        order_task = asyncio.create_task(self.order_manager.start())

        logger.info("[Engine] Conexión WebSocket Alpaca establecida. ¡Engine activo!")

        try:
            # stream.run() es bloqueante - inicia el loop de datos en tiempo real
            # Se ejecuta en un thread separado para no bloquear asyncio
            import threading
            stream_thread = threading.Thread(target=self.stream.run, daemon=True)
            stream_thread.start()
            # Mantener el loop de asyncio activo para las tasks de las estrategias
            while stream_thread.is_alive():
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("[Engine] Apagado manual detectado.")
        finally:
            await self.order_manager.stop()
            order_task.cancel()
            logger.info("[Engine] Engine detenido correctamente.")


# ============================================================
# PUNTO DE ENTRADA
# ============================================================
if __name__ == "__main__":
    if not API_KEY or not SECRET_KEY:
        logger.error("ERROR: ALPACA_API_KEY y ALPACA_SECRET_KEY son requeridas.")
        logger.error("Configúralas en las variables de entorno o en el archivo .env")
        exit(1)

    engine = TradingEngine()
    asyncio.run(engine.run())
