import asyncio
import os
import logging
import threading
from alpaca.data.live.crypto import CryptoDataStream
from engine.order_manager_crypto import OrderManagerCrypto
import talib

logger = logging.getLogger(__name__)

class CryptoTradingEngine:
    def __init__(self):
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

        self.order_manager = OrderManagerCrypto()
        
        # Ignorar requerimientos de Keys de historial (Cripto no lo requiere forzoso)
        self.stream = CryptoDataStream(self.api_key, self.secret_key)
        self.strategies = self._register_strategies()

    def _register_strategies(self):
        from strategies_crypto.strat_01_ema_cross import CryptoEMACrossStrategy
        from strategies_crypto.strat_02_bb_breakout import CryptoBBBreakoutStrategy
        from strategies_crypto.strat_03_grid_spot import CryptoGridSpotStrategy

        return [
            CryptoEMACrossStrategy(self.order_manager),
            CryptoBBBreakoutStrategy(self.order_manager),
            CryptoGridSpotStrategy(self.order_manager)
        ]

    async def _on_crypto_bar(self, bar):
        tasks = []
        for strat in self.strategies:
            if strat.should_process(bar.symbol):
                tasks.append(strat.on_bar(bar))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start_engine(self):
        symbols = list(set([sym for s in self.strategies for sym in s.symbols]))
        logger.info(f"[CryptoEngine] Suscrito a 24/7 en: {symbols}")

        self.stream.subscribe_bars(self._on_crypto_bar, *symbols)
        
        order_task = asyncio.create_task(self.order_manager.start())

        # El stream cripto se corre en background (Thread no bloqueante)
        # Esto permite que coexista con el motor de acciones en el main.py
        stream_thread = threading.Thread(target=self.stream.run, daemon=True)
        stream_thread.start()
        
        logger.info("[CryptoEngine] Conexión Crypto WebSocket (V1Beta3) Activa.")

        try:
            while stream_thread.is_alive():
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Apagado de Cripto")
        finally:
            await self.order_manager.stop()
            order_task.cancel()

# Se exporta la funcion para poder ser llamada desde FastAPI o main original
async def run_crypto_background():
    engine = CryptoTradingEngine()
    await engine.start_engine()

if __name__ == "__main__":
    asyncio.run(run_crypto_background())
