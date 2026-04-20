import logging
import sqlite3
import os
import pandas as pd
from datetime import datetime, timezone
from collections import deque
from ta.momentum import RSIIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoSmartTWAPStrategy(BaseStrategy):
    """
    04 - Smart TWAP Accumulation
    Operación cada 4 horas (en barras de 1H).
    Usa el RSI(14) para modular el tamaño de la compra (DCA).
    Se usa DB SQLite para evitar operaciones repetidas en la misma ventana de 4H tras reinicios.
    """
    RSI_PERIOD = 14
    BASE_ALLOCATION = 50.0

    def __init__(self, order_manager, regime_manager=None):
        # Registramos BTC/USD con un identificador único 
        super().__init__("Smart TWAP Accum", ["BTC/USD"], order_manager)
        self.regime_manager = regime_manager
        self._closes = deque(maxlen=self.RSI_PERIOD * 2)
        
        # [P4 FIX - 2026-04-15] Mapeo explícito a /opt/trader/data para prevenir Split-Brain
        self.db_path = os.environ.get("DB_PATH", "/opt/trader/data/trades.db")
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('''CREATE TABLE IF NOT EXISTS strat_twap_state
                             (id INTEGER PRIMARY KEY, symbol TEXT, last_trade_hour TEXT)''')
                conn.commit()
        except Exception as e:
            logger.error(f"[{self.name}] DB Init Error: {e}")

    def _get_last_trade_hour(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('SELECT last_trade_hour FROM strat_twap_state WHERE symbol=?', ("BTC/USD",))
                row = c.fetchone()
                return row[0] if row else ""
        except:
            return ""

    def _set_last_trade_hour(self, hour_str):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('SELECT id FROM strat_twap_state WHERE symbol=?', ("BTC/USD",))
                if c.fetchone():
                    c.execute('UPDATE strat_twap_state SET last_trade_hour=? WHERE symbol=?', (hour_str, "BTC/USD"))
                else:
                    c.execute('INSERT INTO strat_twap_state (symbol, last_trade_hour) VALUES (?, ?)', ("BTC/USD", hour_str))
                conn.commit()
        except Exception as e:
            logger.error(f"[{self.name}] DB Update Error: {e}")

    async def on_bar(self, bar):

        self._closes.append(bar.close)

        if len(self._closes) < self.RSI_PERIOD:
            return

        # Chequear si es hora divisor de 4 (ej: 00:00, 04:00, 08:00...)
        dt = bar.timestamp
        if dt.hour % 4 != 0:
            return

        # Crear un ID en el formato YYYY-MM-DD-HH
        hour_str = dt.strftime("%Y-%m-%d-%H")
        
        if self._get_last_trade_hour() == hour_str:
            return # Ya se operó en este segmento de 4 horas

        # Calcular RSI
        closes_s = pd.Series(list(self._closes))
        rsi = RSIIndicator(closes_s, window=self.RSI_PERIOD).rsi().iloc[-1]

        # Lógica de asignación matemática (DCA)
        amount_usd = 0
        if rsi < 30:
            amount_usd = self.BASE_ALLOCATION * 2 # Oversold
        elif 30 <= rsi <= 60:
            amount_usd = self.BASE_ALLOCATION # Normal
        else:
            amount_usd = 0 # Overbought, pause

        if amount_usd > 0:
            qty = round(amount_usd / bar.close, 5)
            if qty > 0:
                # Consultar árbitro (P6 = DCA programado, baja prioridad)
                granted = await self.order_manager.request_buy(
                    symbol=bar.symbol, priority=6, strategy_name=self.name
                )
                if not granted:
                    logger.debug(f"[{self.name}] Árbitro denegó. Ventana TWAP omitida.")
                    return

                logger.info(f"[{self.name}] Ejecutando TWAP Táctico. RSI={rsi:.2f}. Compra USD ${amount_usd}")
                await self.order_manager.buy(
                    symbol=bar.symbol,
                    notional_usd=amount_usd,
                    current_price=bar.close,
                    strategy_name=self.name
                )
                self._set_last_trade_hour(hour_str)
                # TWAP es hold de largo plazo: no hay release explícito aquí.
