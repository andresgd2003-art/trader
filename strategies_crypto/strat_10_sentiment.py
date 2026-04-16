import logging
import sqlite3
import os
import aiohttp
import pandas as pd
from ta.momentum import RSIIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoSentimentStrategy(BaseStrategy):
    """
    10 - Sentiment & Fear/Greed Index
    Asset: BTC/USD
    Temporalidad 1D
    API externa: Alternative.me
    Usa SQLite para mantener la serie de cierres diarios.
    """
    RSI_PERIOD = 14

    def __init__(self, order_manager, regime_manager=None):
        super().__init__("Sentiment F&G", ["BTC/USD"], order_manager)
        self.regime_manager = regime_manager
        # [P4 FIX - 2026-04-15] Mapeo explícito a /opt/trader/data para prevenir Split-Brain
        self.db_path = os.environ.get("DB_PATH", "/opt/trader/data/trades.db")
        self._init_db()
        
        self.last_day = -1
        self.closes_1d = []
        self._load_closes()
        
        self.in_position = False
        self.current_qty = 0.0

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('''CREATE TABLE IF NOT EXISTS strat_sentiment_state
                             (id INTEGER PRIMARY KEY, symbol TEXT, date_str TEXT, close_val REAL)''')
                conn.commit()
        except Exception as e:
            logger.error(f"[{self.name}] DB Init Error: {e}")

    def _load_closes(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('SELECT close_val FROM strat_sentiment_state WHERE symbol=? ORDER BY id ASC', ("BTC/USD",))
                self.closes_1d = [row[0] for row in c.fetchall()][-self.RSI_PERIOD * 2:]
        except Exception as e:
            logger.error(f"[{self.name}] DB Load Error: {e}")

    def _save_daily_close(self, date_str, close_val):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('INSERT INTO strat_sentiment_state (symbol, date_str, close_val) VALUES (?, ?, ?)',
                          ("BTC/USD", date_str, close_val))
                conn.commit()
        except:
            pass

    async def fetch_fg_index(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.alternative.me/fng/?limit=1", timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return int(data["data"][0]["value"])
        except Exception as e:
            logger.warning(f"[{self.name}] Error fetching F&G API: {e}")
        return 50 # neutral en caso de error

    async def on_bar(self, bar):
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(10, engine='crypto'):
            return
        dt = bar.timestamp
        # Detectar cierre de día (aprox medianoche o nueva barra dia)
        # Evaluamos F&G a las 00:05 UTC para dejar que el mercado abra nuevo dia
        if dt.hour == 0 and dt.minute == 5 and dt.day != self.last_day:
            self.last_day = dt.day
            
            self.closes_1d.append(bar.close)
            self._save_daily_close(dt.strftime("%Y-%m-%d"), bar.close)

            if len(self.closes_1d) < self.RSI_PERIOD:
                return

            rsi = RSIIndicator(pd.Series(self.closes_1d), window=self.RSI_PERIOD).rsi().iloc[-1]
            fg_index = await self.fetch_fg_index()
            
            logger.info(f"[{self.name}] Chequeo Diario D1. F&G={fg_index}, RSI={rsi:.2f}")

            if not self.in_position:
                if fg_index <= 20 and rsi < 35:
                    # Consultar árbitro (P7 = largo plazo, menor prioridad)
                    granted = await self.order_manager.request_buy(
                        symbol=bar.symbol, priority=7, strategy_name=self.name
                    )
                    if not granted:
                        logger.debug(f"[{self.name}] Árbitro denegó compra BTC. Sentiment omitido.")
                        return

                    logger.info(f"[{self.name}] Extreme Fear detectado + Oversold. Entrando fuerte.")
                    self.in_position = True
                    self.current_qty = round(250.0 / bar.close, 5)
                    await self.order_manager.buy(
                        symbol=bar.symbol,
                        notional_usd=250.0,
                        current_price=bar.close,
                        strategy_name=self.name
                    )
            else:
                if self.current_qty > 0:
                    if fg_index >= 85:
                        logger.info(f"[{self.name}] F&G >= 85. Extreme Greed, liquidando todo.")
                        await self.order_manager.sell_exact(
                            symbol=bar.symbol, exact_qty=self.current_qty, strategy_name=self.name
                        )
                        # Liberar BTC en el árbitro
                        self.order_manager.release_asset(bar.symbol, self.name)
                        self.in_position = False
                        self.current_qty = 0.0
                    elif fg_index >= 75:
                        logger.info(f"[{self.name}] F&G >= 75. Greed. Tomando 50% de ganancia.")
                        sell_qty = round(self.current_qty / 2, 5)
                        self.current_qty -= sell_qty
                        await self.order_manager.sell_exact(
                            symbol=bar.symbol, exact_qty=sell_qty, strategy_name=self.name
                        )
                        # Si ya vendimos todo, liberar
                        if self.current_qty <= 0:
                            self.order_manager.release_asset(bar.symbol, self.name)
                            self.in_position = False
