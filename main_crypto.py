import asyncio
import os
import logging
import threading
import signal
import sys
import time
from alpaca.data.live.crypto import CryptoDataStream
from engine.order_manager_crypto import OrderManagerCrypto
from engine.asset_arbiter import AssetArbiter

logger = logging.getLogger(__name__)

class CryptoTradingEngine:
    def __init__(self):
        self.api_key = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY", "")
        self.secret_key = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")

        # Árbitro centralizado: 1 posición por símbolo, 5 min de cooldown
        self.arbiter = AssetArbiter(cooldown_seconds=300)
        
        self.order_manager = OrderManagerCrypto(arbiter=self.arbiter)

        # RegimeManager compartido — filtra qué bots están activos según Bull/Bear/Chop
        self.regime_manager = None
        try:
            from engine.regime_manager import RegimeManager
            self.regime_manager = RegimeManager()
            logger.info("[CryptoEngine] RegimeManager inyectado en bots crypto ✅")
        except Exception as e:
            logger.warning(f"[CryptoEngine] RegimeManager no disponible (sin filtro de régimen): {e}")
        
        # Ignorar requerimientos de Keys de historial (Cripto no lo requiere forzoso)
        self.stream = CryptoDataStream(self.api_key, self.secret_key)
        self.strategies = self._register_strategies()

    def _register_strategies(self):
        from strategies_crypto.strat_01_ema_cross import CryptoEMACrossStrategy
        from strategies_crypto.strat_02_bb_breakout import CryptoBBBreakoutStrategy
        from strategies_crypto.strat_03_grid_spot import CryptoGridSpotStrategy
        from strategies_crypto.strat_04_smart_twap import CryptoSmartTWAPStrategy
        from strategies_crypto.strat_05_funding_squeeze import CryptoFundingSqueezeStrategy
        from strategies_crypto.strat_06_vol_anomaly import CryptoVolAnomalyStrategy
        from strategies_crypto.strat_07_pair_divergence import CryptoPairDivergenceStrategy
        from strategies_crypto.strat_08_ema_ribbon import CryptoEMARibbonStrategy
        from strategies_crypto.strat_09_vwap_touch import CryptoVWAPTouchStrategy
        from strategies_crypto.strat_10_sentiment import CryptoSentimentStrategy
        # strat_11_vwap_sol_micro ELIMINADA — deadlock con Grid Spot en SOL/USD

        rm = self.regime_manager  # alias corto
        return [
            CryptoEMACrossStrategy(self.order_manager,      regime_manager=rm),
            CryptoBBBreakoutStrategy(self.order_manager,    regime_manager=rm),
            CryptoGridSpotStrategy(self.order_manager,      regime_manager=rm),
            CryptoSmartTWAPStrategy(self.order_manager,     regime_manager=rm),
            CryptoFundingSqueezeStrategy(self.order_manager, regime_manager=rm),
            CryptoVolAnomalyStrategy(self.order_manager,    regime_manager=rm),
            CryptoPairDivergenceStrategy(self.order_manager, regime_manager=rm),
            CryptoEMARibbonStrategy(self.order_manager,     regime_manager=rm),
            CryptoVWAPTouchStrategy(self.order_manager,     regime_manager=rm),
            CryptoSentimentStrategy(self.order_manager,     regime_manager=rm),
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

        # --- HISTORICAL PRE-FETCH (Cold Start Fix) ---
        try:
            from alpaca.data.historical import CryptoHistoricalDataClient
            from alpaca.data.requests import CryptoBarsRequest
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

            hist_client = CryptoHistoricalDataClient()
            from datetime import timezone
            now = datetime.now(timezone.utc)
            req = CryptoBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame.Minute,
                start=now - timedelta(days=5)
            )
            logger.info(f"[CryptoEngine] Descargando historial de 5 días para evitar Cold Start...")
            
            # SUPPRESS ORDERS DURING HISTORY
            self.order_manager.ignore_orders = True
            
            bars = hist_client.get_crypto_bars(req)
            count = 0
            if hasattr(bars, 'data'):
                for sym in symbols:
                    if sym in bars.data:
                        for b in bars.data[sym]:
                            count += 1
                            pb = PseudoBar(sym, b)
                            await self._on_crypto_bar(pb)
            logger.info(f"[CryptoEngine] ✅ Historial inyectado: {count} barras de 1Min procesadas.")
            
            # RESTORE AND RE-SYNC STATE
            self.order_manager.ignore_orders = False
            while not self.order_manager._queue.empty():
                try:
                    self.order_manager._queue.get_nowait()
                    self.order_manager._queue.task_done()
                except: break

            for strat in self.strategies:
                # Reset contadores de entrada escalonada acumulados durante prefetch
                if hasattr(strat, '_bullets_fired'):
                    strat._bullets_fired = 0
                    strat._bullet_qty_total = 0.0
                    logger.debug(f"[CryptoEngine] Reset _bullets_fired para {strat.name}")
                if hasattr(strat, '_tranches'):
                    strat._tranches = []
                    logger.debug(f"[CryptoEngine] Reset _tranches para {strat.name}")
                if hasattr(strat, '_cooldown_until'):
                    strat._cooldown_until = None

                for sym in strat.symbols:
                    pos = strat.sync_position_from_alpaca(sym) > 0
                    if hasattr(strat, "_has_position") and isinstance(strat._has_position, dict):
                        strat._has_position[sym] = pos
                    elif hasattr(strat, "in_position"):
                        strat.in_position = pos
                        
        except Exception as e:
            logger.warning(f"[CryptoEngine] Falló la pre-carga histórica: {e}")
            self.order_manager.ignore_orders = False
        # ----------------------------------------------

        # Configurar cierre limpio
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Envolver wss_client.run() (self.stream.run) en un bucle con backoff
        def _stream_loop():
            while True:
                try:
                    self.stream.run()
                except Exception as e:
                    logger.error(f"[CryptoEngine] Stream abortado: {e}. Reconectando en 5s...")
                    time.sleep(5)

        stream_thread = threading.Thread(target=_stream_loop, daemon=True)
        stream_thread.start()
        
        logger.info("[CryptoEngine] Conexión Crypto WebSocket (V1Beta3) Activa.")

        try:
            while True:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"[CryptoEngine] Error en loop: {e}")
        finally:
            await self.stop()
            order_task.cancel()

    async def stop(self):
        """Detiene el motor cripto de forma limpia."""
        logger.info("[CryptoEngine] Deteniendo stream y motor...")
        try:
            if hasattr(self.stream, 'stop'):
                await self.stream.stop()
            elif hasattr(self.stream, 'close'):
                await self.stream.close()
            logger.info("[CryptoEngine] Stream cripto cerrado.")
        except:
            pass
        await self.order_manager.stop()

# Se exporta la funcion para poder ser llamada desde FastAPI o main original
async def run_crypto_background():
    engine = CryptoTradingEngine()
    await engine.start_engine()

if __name__ == "__main__":
    asyncio.run(run_crypto_background())
