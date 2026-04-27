import logging
import sqlite3
import os
import pandas as pd
from collections import deque
from ta.trend import EMAIndicator
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

class CryptoEMARibbonStrategy(BaseStrategy):
    STRAT_NUMBER = 8

    # FORCED EXIT — safety net evaluado en cada barra (la salida nativa solo en cambio de bloque 4H)
    FORCED_STOP_LOSS_PCT   = 0.04
    FORCED_TAKE_PROFIT_PCT = 0.06
    """
    08 - Multiple EMA Ribbon Pullback
    Asset: BCH/USD
    Timeframe: 4H
    Usa base de datos SQLite para persitir el estado del trend sobre horas/días y no perder confirmaciones.
    """
    def __init__(self, order_manager, regime_manager=None):
        super().__init__("EMA Ribbon Pullback", ["BCH/USD"], order_manager)
        self.regime_manager = regime_manager
        # Necesitamos al menos 55 periodos para el EMA mas largo
        self._closes = deque(maxlen=60)
        self.last_4h_block = -1
        
        self.in_position = False
        self.current_qty = 0.0

        # [P4 FIX - 2026-04-15] Mapeo explícito a /opt/trader/data para prevenir Split-Brain
        self.db_path = os.environ.get("DB_PATH", "/opt/trader/data/trades.db")
        self._init_db()
        self._load_state()
        self._entry_price = {"BCH/USD": 0.0}
        self._has_position = {"BCH/USD": False}
        # ⚠️ ANTI-DUPLICADO: Sincronizar posición real desde Alpaca al reiniciar
        qty = self.sync_position_from_alpaca("BCH/USD")
        if qty > 0:
            self.in_position = True
            self.current_qty = qty
            self._has_position["BCH/USD"] = True
            try:
                pos = self.order_manager.client.get_open_position("BCH/USD")
                self._entry_price["BCH/USD"] = float(pos.avg_entry_price)
            except Exception as e:
                logger.warning(f"[{self.name}] No pude obtener avg_entry_price BCH: {e}")

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('''CREATE TABLE IF NOT EXISTS strat_ribbon_state
                             (id INTEGER PRIMARY KEY, symbol TEXT, close_val REAL, is_aligned INTEGER)''')
                conn.commit()
        except Exception as e:
            logger.error(f"[{self.name}] DB Init Error: {e}")

    def _save_close(self, close_val, is_aligned):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('INSERT INTO strat_ribbon_state (symbol, close_val, is_aligned) VALUES (?, ?, ?)',
                          ("BCH/USD", close_val, 1 if is_aligned else 0))
                
                # Conservar maximo 60 registros
                c.execute('DELETE FROM strat_ribbon_state WHERE id NOT IN (SELECT id FROM strat_ribbon_state ORDER BY id DESC LIMIT 60)')
                conn.commit()
        except Exception as e:
            logger.error(f"[{self.name}] DB Update Error: {e}")

    def _load_state(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute('SELECT close_val FROM strat_ribbon_state WHERE symbol=? ORDER BY id ASC', ("BCH/USD",))
                rows = c.fetchall()
                for r in rows:
                    self._closes.append(r[0])
        except Exception as e:
            logger.error(f"[{self.name}] DB Load Error: {e}")

    async def on_bar(self, bar):
        if not self.should_process(bar.symbol):
            return

        symbol = bar.symbol
        # FORCED EXIT — evaluado en cada barra (la salida nativa solo dispara cada 4H con cross EMA)
        if self._has_position.get(symbol) and self._entry_price.get(symbol, 0) > 0:
            current_close = float(bar.close)
            entry = self._entry_price[symbol]
            ret = (current_close / entry) - 1.0
            if ret <= -self.FORCED_STOP_LOSS_PCT or ret >= self.FORCED_TAKE_PROFIT_PCT:
                tag = "🛑 FORCED SL" if ret < 0 else "💰 FORCED TP"
                logger.info(f"[{self.name}] {tag} {ret*100:+.2f}% → SELL {symbol}")
                try:
                    real_qty = self.sync_position_from_alpaca(symbol) or self.current_qty
                    if real_qty > 0:
                        await self.order_manager.sell_exact(
                            symbol=symbol, exact_qty=real_qty, strategy_name=self.name
                        )
                        self.order_manager.release_asset(symbol, self.name)
                except Exception as e:
                    logger.error(f"[{self.name}] Error en forced exit: {e}")
                self.in_position = False
                self.current_qty = 0.0
                self._has_position[symbol] = False
                self._entry_price[symbol] = 0.0
                return

        dt = bar.timestamp
        # Evaluamos solo 1 vez cada 4 horas
        hour_block = dt.hour // 4 
        
        # Guardaremos los cierres de forma sintética (cada que toque el bloque 4h)
        if hour_block != self.last_4h_block:
            self.last_4h_block = hour_block
            self._closes.append(bar.close)

            closes_s = pd.Series(list(self._closes))
            
            # Solo procesamos si hay datos suficientes (55 periodos para EMA más largo)
            if len(self._closes) >= 55:
                e8 = EMAIndicator(closes_s, window=8).ema_indicator().iloc[-1]
                e21 = EMAIndicator(closes_s, window=21).ema_indicator().iloc[-1]
                e55 = EMAIndicator(closes_s, window=55).ema_indicator().iloc[-1]
                
                # Simplificado: 3 EMAs en vez de 5 → señal más alcanzable
                is_aligned = e8 > e21 and e21 > e55
                self._save_close(bar.close, is_aligned)

                if self.in_position:
                    if e8 < e55:  # EMA rápida cruza la lenta → fin de tendencia
                        logger.info(f"[{self.name}] EMA8 cruzó EMA55 hacia abajo. Fin de Ribbon. Vendiendo.")
                        # Usar qty REAL de Alpaca, no el tracking interno
                        real_qty = self.sync_position_from_alpaca(bar.symbol)
                        if real_qty > 0:
                            await self.order_manager.sell_exact(
                                symbol=bar.symbol, exact_qty=real_qty, strategy_name=self.name
                            )
                            # Liberar BCH en el árbitro
                            self.order_manager.release_asset(bar.symbol, self.name)
                        self.in_position = False
                        self.current_qty = 0.0
                        self._has_position[bar.symbol] = False
                        self._entry_price[bar.symbol] = 0.0
                else:
                    if is_aligned and bar.close <= e21:  # Pullback a value zone (EMA21)
                        # Consultar árbitro (P5 = EMA ribbon 4H)
                        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
                        granted = await self.order_manager.request_buy(
                            symbol=bar.symbol, priority=5, strategy_name=self.name
                        )
                        if not granted:
                            logger.debug(f"[{self.name}] Árbitro denegó compra BCH. Ribbon omitido.")
                        else:
                            logger.info(f"[{self.name}] Bullish Alignment (3-EMA) + Pullback al EMA21. Comprando!")
                            self.in_position = True
                            self._has_position[bar.symbol] = True
                            self._entry_price[bar.symbol] = float(bar.close)
                            # Calcular qty basándose en el cap REAL del OrderManager
                            cap = self.order_manager._get_dynamic_cap()
                            self.current_qty = round(cap / float(bar.close), 5)
                            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
                            await self.order_manager.buy(
                                symbol=bar.symbol,
                                notional_usd=cap,
                                current_price=bar.close,
                                strategy_name=self.name
                            )
            else:
                self._save_close(bar.close, 0)
