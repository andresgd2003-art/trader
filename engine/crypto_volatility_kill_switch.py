"""
engine/crypto_volatility_kill_switch.py
========================================
Kill-switch de volatilidad para crypto.

Cripto no tiene un VIX oficial. Aproximamos con el ATR (Average True Range)
de BTC/USD en barras de 5m: si ATR(14) / close > UMBRAL → mercado en pánico,
todas las estrategias crypto pausan ENTRADAS nuevas (las salidas siguen
evaluándose para proteger capital ya invertido).

Uso desde una strategy:
    from engine.crypto_volatility_kill_switch import is_crypto_panic
    if is_crypto_panic():
        return  # NO entrar nueva posición

Uso desde el OrderManager (más global):
    if is_crypto_panic():
        logger.warning(...)
        return  # rechazar BUY antes de enviar a Alpaca
"""
from __future__ import annotations
import logging
import math
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Estado global compartido — actualizado por feed_btc_bar() desde el engine
_HIGH = deque(maxlen=200)
_LOW = deque(maxlen=200)
_CLOSE = deque(maxlen=200)
_LAST_RATIO: float = 0.0
_LAST_UPDATE: float = 0.0
_LAST_LOG: float = 0.0

# Umbral: ATR/Close > 1.2% en 5m equivale a un movimiento intradía agresivo
# (≈BTC moviéndose >$900 en una vela cuando vale $75K).
ATR_PERIOD = 14
PANIC_RATIO_THRESHOLD = 0.012  # 1.2 %


def feed_btc_bar(high: float, low: float, close: float) -> None:
    """Llamar desde el engine de crypto cada vez que llega una barra de BTC/USD."""
    global _LAST_UPDATE
    if any(v is None or math.isnan(float(v)) for v in (high, low, close)):
        return
    _HIGH.append(float(high))
    _LOW.append(float(low))
    _CLOSE.append(float(close))
    _LAST_UPDATE = time.time()


def _compute_atr_ratio() -> float:
    """ATR(14) / Close. Devuelve 0.0 si no hay datos suficientes."""
    n = len(_CLOSE)
    if n < ATR_PERIOD + 1:
        return 0.0
    trs = []
    for i in range(n - ATR_PERIOD, n):
        h, l = _HIGH[i], _LOW[i]
        prev_c = _CLOSE[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    atr = sum(trs) / len(trs)
    last_close = _CLOSE[-1]
    if last_close <= 0:
        return 0.0
    return atr / last_close


def is_crypto_panic() -> bool:
    """True si la volatilidad de BTC sugiere pánico → pausar entradas crypto."""
    global _LAST_RATIO, _LAST_LOG
    ratio = _compute_atr_ratio()
    _LAST_RATIO = ratio
    is_panic = ratio > PANIC_RATIO_THRESHOLD
    # Log con throttle (1 vez por minuto máximo) para visibilidad
    now = time.time()
    if is_panic and (now - _LAST_LOG) > 60:
        logger.warning(
            f"[CryptoKillSwitch] 🚨 PÁNICO BTC ATR/close={ratio*100:.2f}% > "
            f"{PANIC_RATIO_THRESHOLD*100:.2f}% — entradas crypto PAUSADAS"
        )
        _LAST_LOG = now
    return is_panic


def get_state() -> dict:
    """Snapshot para dashboard / debug."""
    return {
        "atr_ratio": _LAST_RATIO,
        "threshold": PANIC_RATIO_THRESHOLD,
        "is_panic": _LAST_RATIO > PANIC_RATIO_THRESHOLD,
        "bars_in_buffer": len(_CLOSE),
        "last_update_age_sec": time.time() - _LAST_UPDATE if _LAST_UPDATE else None,
    }
