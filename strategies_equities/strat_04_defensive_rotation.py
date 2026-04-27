"""
strat_04_defensive_rotation.py — Defensive Rotation (LONG-only)
================================================================
Régimen: BEAR | CHOP
Universo: KO, PG, JNJ, WMT, PEP (defensive dividend stocks)
Timeframe: 5m bars (SPY como tracker de RSI macro)

Lógica:
  1. RSI(14) sobre SPY intradía.
  2. Si régimen es BULL → no actuar (salida early).
  3. Si régimen es BEAR o CHOP y RSI_SPY < 40:
       - Calcular RSI(14) propio de cada ticker defensivo.
       - Seleccionar el ticker con RSI más bajo SIN posición abierta.
       - BUY bracket (long) con TP +2% / SL -3%.
  4. Salida (siempre evaluada):
       - RSI_SPY > 55  OR  régimen volvió BULL  OR  take_profit +2% alcanzado
         → cerrar posición.

Compatible con short firewall: SOLO LONG. Nunca llama a sell_short.
client_order_id: prefijo "eq_" lo genera OrderManagerEquities automáticamente.
"""
import logging
from collections import deque

import pandas as pd
from ta.momentum import RSIIndicator

from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

DEFENSIVE_UNIVERSE = ["KO", "PG", "JNJ", "WMT", "PEP"]
SPY_TRACKER = "SPY"


class DefensiveRotation(BaseStrategy):
    STRAT_NUMBER = 4
    RSI_PERIOD = 14
    RSI_SPY_BUY_THRESHOLD = 45.0  # Bajado de 40 → 45 para más oportunidades de rotación defensiva
    RSI_SPY_EXIT_THRESHOLD = 55.0
    TAKE_PROFIT_PCT = 0.02
    STOP_LOSS_PCT = 0.03
    HEARTBEAT_BARS = 10

    def __init__(self, order_manager, regime_manager=None):
        symbols = DEFENSIVE_UNIVERSE + [SPY_TRACKER]
        super().__init__(
            name="DefensiveRotation",
            symbols=symbols,
            order_manager=order_manager,
        )
        self.regime_manager = regime_manager

        # Precio histórico para RSI (necesitamos al menos RSI_PERIOD+1 barras)
        self._closes: dict[str, deque] = {
            s: deque(maxlen=max(50, self.RSI_PERIOD + 5)) for s in symbols
        }

        # Tracking de posiciones: entry_price para TP y has_position
        self._has_position: dict[str, bool] = {s: False for s in DEFENSIVE_UNIVERSE}
        self._entry_price: dict[str, float] = {s: 0.0 for s in DEFENSIVE_UNIVERSE}

        # Sync inicial desde Alpaca (anti-duplicado al reiniciar)
        for sym in DEFENSIVE_UNIVERSE:
            qty = self.sync_position_from_alpaca(sym)
            if qty > 0:
                self._has_position[sym] = True

        self._bar_counter = 0

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────
    def _rsi(self, symbol: str) -> float | None:
        closes = list(self._closes.get(symbol, []))
        if len(closes) < self.RSI_PERIOD + 1:
            return None
        try:
            return float(
                RSIIndicator(pd.Series(closes), window=self.RSI_PERIOD).rsi().iloc[-1]
            )
        except Exception:
            return None

    def _current_regime(self) -> str:
        if not self.regime_manager:
            return "UNKNOWN"
        try:
            from engine.regime_manager import get_current_regime
            return get_current_regime().get("regime", "UNKNOWN")
        except Exception:
            return "UNKNOWN"

    # ──────────────────────────────────────────────────────────────
    # Main handler
    # ──────────────────────────────────────────────────────────────
    async def on_bar(self, bar) -> None:
        sym = bar.symbol
        if not self.should_process(sym):
            return

        close = float(bar.close)
        self._closes[sym].append(close)
        self._bar_counter += 1

        # Necesitamos datos de SPY suficientes para calcular RSI
        rsi_spy = self._rsi(SPY_TRACKER)
        if rsi_spy is None:
            return

        regime = self._current_regime()

        # Heartbeat log cada ~10 barras
        if self._bar_counter % self.HEARTBEAT_BARS == 0:
            logger.info(
                f"[DefensiveRotation] RSI_SPY={rsi_spy:.2f} regimen={regime} "
                f"→ evaluando rotacion"
            )

        # ─── SALIDAS (siempre evaluadas, sin Soft Guard) ──────────
        # Condición de salida global: RSI_SPY > 55 o régimen volvió BULL
        global_exit = rsi_spy > self.RSI_SPY_EXIT_THRESHOLD or regime == "BULL"

        for s in DEFENSIVE_UNIVERSE:
            if not self._has_position.get(s):
                continue

            # Evaluar TP +2% usando último close conocido del ticker
            last_close = self._closes[s][-1] if self._closes[s] else 0.0
            entry = self._entry_price.get(s, 0.0)
            tp_hit = (
                entry > 0.0
                and last_close > 0.0
                and last_close >= entry * (1 + self.TAKE_PROFIT_PCT)
            )

            if global_exit or tp_hit:
                reason = (
                    "RSI_SPY>55" if rsi_spy > self.RSI_SPY_EXIT_THRESHOLD
                    else "regime=BULL" if regime == "BULL"
                    else f"TP+{self.TAKE_PROFIT_PCT*100:.1f}%"
                )
                logger.info(
                    f"[{self.name}] 🔴 SALIDA {s} | motivo={reason} | "
                    f"last={last_close:.2f} entry={entry:.2f} RSI_SPY={rsi_spy:.2f}"
                )
                await self.order_manager.close_position(s, None, self.name)
                self._has_position[s] = False
                self._entry_price[s] = 0.0

        # ─── ENTRADAS: salida early si régimen es BULL ───────────
        if regime == "BULL":
            return

        # Sólo entrar si BEAR/CHOP y RSI_SPY < 40
        if regime not in ("BEAR", "CHOP"):
            return
        if rsi_spy >= self.RSI_SPY_BUY_THRESHOLD:
            return

        # Soft Guard obligatorio antes de emitir BUY
        if self.regime_manager and not self.regime_manager.is_strategy_enabled(
            self.STRAT_NUMBER, engine="equities"
        ):
            return

        # Calcular RSI de cada defensivo SIN posición abierta
        candidates: list[tuple[str, float]] = []
        for s in DEFENSIVE_UNIVERSE:
            if self._has_position.get(s):
                continue
            rsi_s = self._rsi(s)
            if rsi_s is None:
                continue
            candidates.append((s, rsi_s))

        if not candidates:
            return

        # Seleccionar el ticker con RSI más bajo (más sobrevendido)
        target, target_rsi = min(candidates, key=lambda x: x[1])
        target_price = self._closes[target][-1] if self._closes[target] else 0.0
        if target_price <= 0:
            return

        logger.info(
            f"[{self.name}] 🟢 ROTACIÓN DEFENSIVA | regimen={regime} "
            f"RSI_SPY={rsi_spy:.2f} → BUY {target} "
            f"(RSI={target_rsi:.2f}, px=${target_price:.2f})"
        )

        await self.order_manager.buy_bracket(
            symbol=target,
            price=target_price,
            stop_loss_pct=self.STOP_LOSS_PCT,
            take_profit_pct=self.TAKE_PROFIT_PCT,
            strategy_name=self.name,
        )
        self._has_position[target] = True
        self._entry_price[target] = target_price

    def on_market_open(self):
        # Re-sincronizar posiciones al abrir el mercado
        for sym in DEFENSIVE_UNIVERSE:
            qty = self.sync_position_from_alpaca(sym)
            self._has_position[sym] = qty > 0
            if qty == 0:
                self._entry_price[sym] = 0.0
