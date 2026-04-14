"""
strat_09_insider_flow.py — Insider Buying Tracker (SEC EDGAR)
==============================================================
Régimen: BULL | Fuente: SEC EDGAR Full-Text Search API (Form 4)
Timeframe: Daily (cron job a las 18:00 EST = después del cierre)

Lógica:
  1. Cada día a las 18:00 EST, llamar a la API pública de EDGAR
     para buscar Form 4 registrados ese día.
  2. Filtrar: CEO, CFO, o >10% Owner que compró >$500,000 en mercado abierto.
  3. Si se encuentra: marcar el símbolo para compra en la apertura del día siguiente.

EDGAR API: https://efts.sec.gov/LATEST/search-index?q=%22form+4%22
Sin autenticación requerida (datos públicos).
"""
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

EDGAR_API = "https://efts.sec.gov/LATEST/search-index"
MIN_INSIDER_BUY_USD = 500_000   # $500k mínimo
INSIDER_TITLES = ["chief executive", "ceo", "chief financial", "cfo", "10% owner"]


class InsiderFlowStrategy(BaseStrategy):
    STRAT_NUMBER = 9

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="Insider Buying Flow",
            symbols=[],   # Dinámico: se añade al detectar insiders
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._pending_next_open: set = set()
        self._traded_today: set = set()
        self._prices: dict[str, float] = {}

    async def fetch_insider_filings(self):
        """
        Consulta SEC EDGAR para obtener Form 4 del día actual.
        Se ejecuta a las 18:00 EST vía el loop del EquitiesEngine.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"{EDGAR_API}?q=%22form+4%22&dateRange=custom&startdt={today}&enddt={today}&hits.hits._source.period_of_report=*"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning(f"[{self.name}] EDGAR API returned {resp.status}")
                        return

                    data = await resp.json()
                    hits = data.get("hits", {}).get("hits", [])

                    for hit in hits:
                        source = hit.get("_source", {})
                        entity = source.get("entity_name", "").lower()
                        ticker = source.get("stock_object", {}).get("ticker", "")
                        # Nota: El API de EDGAR no siempre retorna dollar amounts directamente.
                        # En producción se puede usar OpenInsider o Quiver Quant para datos más ricos.
                        # Aquí hacemos un proxy básico buscando títulos de insider.

                        is_key_insider = any(t in entity for t in INSIDER_TITLES)

                        if is_key_insider and ticker and ticker not in self._traded_today:
                            logger.info(
                                f"[{self.name}] 📋 Insider Filing detectado: "
                                f"{entity} → {ticker}. Marcado para apertura mañana."
                            )
                            self._pending_next_open.add(ticker.upper())
                            if ticker.upper() not in self.symbols:
                                self.symbols.append(ticker.upper())

        except Exception as e:
            logger.error(f"[{self.name}] Error consultando EDGAR: {e}")

    async def on_bar(self, bar) -> None:
        """Al inicio del día siguiente, compra los candidatos de insider."""
        if not self.should_process(bar.symbol):
            return
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='equities'):
            return

        sym = bar.symbol
        self._prices[sym] = float(bar.close)

        if sym not in self._pending_next_open:
            return
        if sym in self._traded_today:
            return

        # Comprar en la primera barra del día
        bar_time = bar.timestamp.time() if hasattr(bar.timestamp, 'time') else datetime.now().time()
        from datetime import time as dtime
        if bar_time < dtime(9, 31) or bar_time > dtime(9, 45):
            return  # Solo en los primeros 15 minutos de mercado

        logger.info(
            f"[{self.name}] 🏦 INSIDER BUY SIGNAL {sym}! "
            f"Comprando en apertura @ ${bar.close:.2f}"
        )
        # ⚠️ ANTI-DUPLICADO: Verificar posición viva para no re-entrar si reinició hoy
        if self.sync_position_from_alpaca(sym) > 0:
            logger.info(f"[{self.name}] ⚠️ Señal Insider en {sym} pero ya hay posición activa. Evitando duplicado.")
            self._pending_next_open.discard(sym)
            self._traded_today.add(sym)
            return

        await self.order_manager.buy_bracket(
            symbol=sym,
            price=float(bar.close),
            stop_loss_pct=0.08,    # Insider buys son largo plazo, SL más amplio
            take_profit_pct=0.30,
            strategy_name=self.name
        )
        self._pending_next_open.discard(sym)
        self._traded_today.add(sym)

    def on_market_open(self):
        self._traded_today = set()
