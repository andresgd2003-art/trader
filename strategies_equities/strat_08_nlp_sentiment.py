"""
strat_08_nlp_sentiment.py — News Sentiment + FinBERT-lite
==========================================================
Régimen: BULL | Fuente: Alpaca News WebSocket
Timeframe: Real-time (headline driven)

Modelo: yiyanghkust/finbert-tone (~270MB, DistilBERT financiero)
  - Labels: "Positive" | "Negative" | "Neutral"
  - Score > 0.80 con vela 1min de volumen 3x → BUY

El modelo se carga en memoria de forma lazy (solo cuando llega el primer headline).
Controlado por variable de entorno ENABLE_NLP=true (default: true).
"""
import os
import logging
import asyncio
from collections import deque
from datetime import time as dtime
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

ENABLE_NLP = os.environ.get("ENABLE_NLP", "true").lower() == "true"
NLP_MODEL = "yiyanghkust/finbert-tone"
SENTIMENT_THRESHOLD = 0.80   # Score mínimo para considerar positivo
VOL_CONFIRMATION = 3.0       # Volumen 1min debe ser 3x el promedio

# Diccionario de keywords como fallback si NLP no está disponible
BULLISH_KEYWORDS = {
    "beats": 0.9, "record earnings": 1.0, "raises guidance": 0.95,
    "fda approval": 1.0, "fda approved": 1.0, "buyback": 0.75,
    "partnership": 0.65, "upgrade": 0.75, "exceeds estimates": 0.85,
    "breakthrough": 0.80, "acquisition": 0.60, "strong demand": 0.70,
    "raised dividend": 0.80, "share repurchase": 0.75, "beat expectations": 0.90,
    "surpasses": 0.80, "record revenue": 0.90,
}
BEARISH_KEYWORDS = {
    "misses estimates": -0.9, "guidance cut": -1.0, "recall": -0.85,
    "sec investigation": -1.0, "bankruptcy": -1.0, "downgrade": -0.75,
    "disappoints": -0.80, "below expectations": -0.85, "lays off": -0.70,
    "job cuts": -0.70, "fraud": -0.95, "lawsuit": -0.65, "warning": -0.60,
}


def score_with_keywords(text: str) -> float:
    """Scoring de sentimiento via keywords ponderados (-1 a +1)."""
    text_lower = text.lower()
    score = 0.0
    count = 0
    for kw, w in BULLISH_KEYWORDS.items():
        if kw in text_lower:
            score += w
            count += 1
    for kw, w in BEARISH_KEYWORDS.items():
        if kw in text_lower:
            score += w
            count += 1
    return score / max(count, 1) if count > 0 else 0.0


class NLPSentimentStrategy(BaseStrategy):
    STRAT_NUMBER = 8

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="NLP News Sentiment",
            symbols=[],   # Dinámico: se asigna cuando llega un headline
            order_manager=order_manager
        )
        self.regime_manager = regime_manager
        self._pipeline = None   # Lazy load del modelo NLP
        self._volumes: dict[str, deque] = {}
        self._traded_today: set = set()
        self._pending_buy: set = set()  # Símbolos esperando confirmación de vol

        # Intentar cargar el modelo en background
        if ENABLE_NLP:
            asyncio.get_event_loop().run_in_executor(None, self._load_model)

    def _load_model(self):
        """Carga el modelo FinBERT-tone de forma lazy (no bloquea el event loop)."""
        try:
            from transformers import pipeline
            logger.info(f"[{self.name}] Cargando modelo NLP: {NLP_MODEL}...")
            self._pipeline = pipeline(
                "text-classification",
                model=NLP_MODEL,
                top_k=1,
                truncation=True,
                max_length=128,
                device=-1   # CPU siempre
            )
            logger.info(f"[{self.name}] ✅ Modelo NLP cargado correctamente.")
        except Exception as e:
            logger.warning(f"[{self.name}] ⚠️ No se pudo cargar NLP: {e}. Usando keywords.")

    def _get_sentiment(self, text: str) -> float:
        """
        Retorna score de sentimiento entre -1.0 y +1.0.
        Usa FinBERT si está disponible, keywords como fallback.
        """
        if self._pipeline:
            try:
                result = self._pipeline(text)[0][0]
                label = result["label"].lower()
                score = result["score"]
                if label == "positive":
                    return score
                elif label == "negative":
                    return -score
                return 0.0
            except Exception:
                pass
        return score_with_keywords(text)

    def on_news(self, symbol: str, headline: str):
        """
        Llamado cuando llega un headline del Alpaca News WebSocket.
        Se ejecuta de forma síncrona (llamado desde el dispatcher).
        """
        if not self.is_active:
            return
        if symbol in self._traded_today:
            return

        score = self._get_sentiment(headline)

        logger.debug(f"[{self.name}] Headline '{headline[:60]}...' Score={score:.3f}")

        if score >= SENTIMENT_THRESHOLD:
            self._pending_buy.add(symbol)
            if symbol not in self.symbols:
                self.symbols.append(symbol)
            logger.info(f"[{self.name}] 📰 HIGH POSITIVE sentiment {symbol}: {score:.3f}")

    async def on_bar(self, bar) -> None:
        """Confirma la señal de news con volumen 3x en la siguiente vela de 1min."""
        if not self.should_process(bar.symbol):
            return

        sym = bar.symbol
        vol = float(bar.volume)

        if sym not in self._volumes:
            self._volumes[sym] = deque(maxlen=25)
        self._volumes[sym].append(vol)

        if sym not in self._pending_buy:
            return
        if sym in self._traded_today:
            return

        if len(self._volumes[sym]) < 5:
            return

        vol_avg = sum(list(self._volumes[sym])[:-1]) / max(len(self._volumes[sym]) - 1, 1)

        if vol >= vol_avg * VOL_CONFIRMATION:
            # ⚠️ ANTI-DUPLICADO: Verificar posición viva para no re-entrar si reinició hoy
            if self.sync_position_from_alpaca(sym) > 0:
                logger.info(f"[{self.name}] ⚠️ Volumen confirmado en {sym} pero ya hay posición activa. Evitando duplicado.")
                self._pending_buy.discard(sym)
                self._traded_today.add(sym)
                return

            logger.info(
                f"[{self.name}] ✅ Volume confirmed {sym}! "
                f"Vol={vol:.0f} ({vol/vol_avg:.1f}x). Comprando."
            )
            await self.order_manager.buy_bracket(
                symbol=sym,
                price=float(bar.close),
                stop_loss_pct=0.05,
                take_profit_pct=0.15,
                strategy_name=self.name
            )
            self._pending_buy.discard(sym)
            self._traded_today.add(sym)

    def on_market_open(self):
        self._pending_buy = set()
        self._traded_today = set()
        self._volumes = {}
