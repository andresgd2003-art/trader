"""
engine/stock_scorer.py
=======================
Propuesta C — us-stock-analysis Skill

Scoring dinámico de candidatos de acciones para el motor Equities.
Solo se activa cuando el DailyModeManager tiene el modo C activo.

CRITERIOS DE SCORING (0–100 puntos):
  1. Momentum 3M vs SPY (30 pts):
     - Retorno > SPY+5%  → 30 pts
     - Retorno > SPY      → 15 pts
     - Retorno < SPY      → 0 pts
  2. RSI 14 en zona saludable (20 pts):
     - RSI 50–70          → 20 pts (sweet spot: subiendo sin sobrecomprar)
     - RSI 40–80          → 10 pts (zona aceptable)
     - Fuera de rango     → 0 pts
  3. Volumen relativo vs promedio 20D (20 pts):
     - Vol > 1.5x avg     → 20 pts (interés institucional)
     - Vol > 1.0x avg     → 10 pts
     - Vol < 1.0x avg     → 0 pts
  4. Penalización por earnings próximos (−20 pts):
     - Earnings en < 7D   → −20 pts
     - Earnings en 7–14D  → −10 pts
  5. Tendencia de precio sobre SMA50 (+10 pts):
     - Precio > SMA50     → +10 pts

PERSISTENCIA:
  Los scores se guardan en SQLite /app/data/stock_scores.db
  La tabla `scores` contiene: symbol, score, timestamp, y desglose por criterio.
"""
import logging
import sqlite3
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATA_PATH", "/opt/trader/data")) / "stock_scores.db"


# ── Universo de acciones a evaluar ──────────────────────────────────────────
# Basado en el skill us-stock-analysis: growth + large cap + quality
DEFAULT_UNIVERSE = [
    # Tech / Growth
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "CRM", "ORCL",
    # Financials
    "JPM", "BAC", "GS", "V", "MA", "AXP",
    # Healthcare
    "UNH", "LLY", "JNJ", "ABBV", "PFE",
    # Energy / Materials
    "XOM", "CVX", "COP",
    # Industrials
    "CAT", "DE", "HON",
    # ETFs que pueden servir como candidatos de sector
    "QQQ", "SPY", "XLK", "XLF", "XLE",
]


class StockScorer:
    """
    Calcula scores cuantitativos para un universo de acciones.
    Diseñado para correr semanalmente como job de fondo.
    """

    def __init__(self):
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Crea la tabla de scores si no existe."""
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('PRAGMA synchronous=NORMAL;')
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS scores (
                        symbol      TEXT NOT NULL,
                        score       REAL NOT NULL,
                        momentum    REAL DEFAULT 0,
                        rsi_score   REAL DEFAULT 0,
                        vol_score   REAL DEFAULT 0,
                        trend_score REAL DEFAULT 0,
                        earnings_pen REAL DEFAULT 0,
                        timestamp   TEXT NOT NULL,
                        PRIMARY KEY (symbol, timestamp)
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"[StockScorer] Error creando DB: {e}")

    async def score_universe(self, symbols: Optional[list] = None) -> dict[str, float]:
        """
        Calcula el score de cada símbolo en el universo.
        
        Returns:
            {symbol: score} ordenado de mayor a menor.
        """
        import asyncio
        target = symbols or DEFAULT_UNIVERSE
        scores = {}

        for symbol in target:
            try:
                score, breakdown = await asyncio.get_event_loop().run_in_executor(
                    None, self._score_symbol, symbol
                )
                scores[symbol] = score
                await self._persist_score(symbol, score, breakdown)
                logger.info(f"[StockScorer] {symbol}: {score:.1f}/100 | {breakdown}")
                await asyncio.sleep(0.3)  # Rate limit gentil
            except Exception as e:
                logger.warning(f"[StockScorer] Error scoring {symbol}: {e}")
                scores[symbol] = 0.0

        return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))

    def _score_symbol(self, symbol: str) -> tuple[float, dict]:
        """
        Scoring sincrónico para un símbolo. Usa el SDK de Alpaca.
        Retorna (score_total, breakdown_dict).
        """
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        api_key    = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        client = StockHistoricalDataClient(api_key, secret_key)

        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=95)  # ~90 días hábiles

        breakdown = {"momentum": 0, "rsi": 0, "volume": 0, "trend": 0, "earnings_pen": 0}

        try:
            req = StockBarsRequest(
                symbol_or_symbols=[symbol, "SPY"],
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                limit=95
            )
            bars = client.get_stock_bars(req)

            sym_bars = bars.get(symbol, [])
            spy_bars = bars.get("SPY", [])

            if len(sym_bars) < 20 or len(spy_bars) < 20:
                return 0.0, breakdown

            closes_sym = [float(b.close) for b in sym_bars]
            closes_spy = [float(b.close) for b in spy_bars]
            volumes    = [float(b.volume) for b in sym_bars]

            # ── 1. Momentum 3M ──
            ret_sym = (closes_sym[-1] - closes_sym[0]) / closes_sym[0] * 100
            ret_spy = (closes_spy[-1] - closes_spy[0]) / closes_spy[0] * 100
            if ret_sym > ret_spy + 5:
                breakdown["momentum"] = 30
            elif ret_sym > ret_spy:
                breakdown["momentum"] = 15

            # ── 2. RSI 14 ──
            rsi = self._calc_rsi(closes_sym, period=14)
            if 50 <= rsi <= 70:
                breakdown["rsi"] = 20
            elif 40 <= rsi <= 80:
                breakdown["rsi"] = 10

            # ── 3. Volumen Relativo ──
            if len(volumes) >= 20:
                avg_vol_20d = sum(volumes[-20:]) / 20
                last_vol = volumes[-1]
                rel_vol = last_vol / avg_vol_20d if avg_vol_20d > 0 else 1.0
                if rel_vol >= 1.5:
                    breakdown["volume"] = 20
                elif rel_vol >= 1.0:
                    breakdown["volume"] = 10

            # ── 4. Tendencia vs SMA50 ──
            if len(closes_sym) >= 50:
                sma50 = sum(closes_sym[-50:]) / 50
                if closes_sym[-1] > sma50:
                    breakdown["trend"] = 10

            # ── 5. Penalización por earnings (proxy: no tenemos datos, defaultear 0) ──
            # En una implementación real, usar el endpoint corporate actions de Alpaca
            breakdown["earnings_pen"] = 0

        except Exception as e:
            logger.debug(f"[StockScorer] Error calculando {symbol}: {e}")

        total = sum(breakdown.values())
        return min(max(total, 0), 100), breakdown

    def _calc_rsi(self, closes: list, period: int = 14) -> float:
        """Calcula el RSI sin dependencias externas."""
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    async def _persist_score(self, symbol: str, score: float, breakdown: dict) -> None:
        """Guarda el score en SQLite con reintentos asíncronos."""
        import asyncio
        max_retries = 5
        for attempt in range(max_retries):
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    conn.execute('PRAGMA journal_mode=WAL;')
                    conn.execute('PRAGMA synchronous=NORMAL;')
                    ts = datetime.now(timezone.utc).isoformat()
                    conn.execute("""
                        INSERT OR REPLACE INTO scores
                        (symbol, score, momentum, rsi_score, vol_score, trend_score, earnings_pen, timestamp)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        symbol, round(score, 2),
                        breakdown.get("momentum", 0),
                        breakdown.get("rsi", 0),
                        breakdown.get("volume", 0),
                        breakdown.get("trend", 0),
                        breakdown.get("earnings_pen", 0),
                        ts,
                    ))
                    conn.commit()
                return # Éxito
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(f"[StockScorer] DB bloqueada para {symbol}. Reintento {attempt+1}/{max_retries}...")
                    await asyncio.sleep(0.5)
                else:
                    logger.error(f"[StockScorer] Error persistiendo {symbol}: {e}")
                    break
            except Exception as e:
                logger.error(f"[StockScorer] Error inesperado persistiendo {symbol}: {e}")
                break

    def get_top_scores(self, limit: int = 20) -> list:
        """Retorna los top N símbolos por score desde la DB."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('PRAGMA synchronous=NORMAL;')
                cursor = conn.execute("""
                    SELECT symbol, score, momentum, rsi_score, vol_score, trend_score, earnings_pen, timestamp
                    FROM scores
                    WHERE timestamp = (SELECT MAX(timestamp) FROM scores)
                    ORDER BY score DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                return [
                    {
                        "symbol":    r[0],
                        "score":     r[1],
                        "breakdown": {
                            "momentum":     r[2],
                            "rsi":          r[3],
                            "volume":       r[4],
                            "trend":        r[5],
                            "earnings_pen": r[6],
                        },
                        "timestamp": r[7],
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"[StockScorer] Error leyendo DB: {e}")
            return []

    def get_symbols_above(self, min_score: float = 60.0) -> list[str]:
        """Retorna símbolos con score mayor al umbral (para filtrar candidatos en modo C)."""
        return [s["symbol"] for s in self.get_top_scores(limit=50) if s["score"] >= min_score]


# ── Singleton global ──────────────────────────────────────────────────────────
_SCORER_INSTANCE: Optional[StockScorer] = None


def get_scorer() -> StockScorer:
    global _SCORER_INSTANCE
    if _SCORER_INSTANCE is None:
        _SCORER_INSTANCE = StockScorer()
    return _SCORER_INSTANCE
