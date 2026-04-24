"""
strategies/strat_11_inverse_momentum.py — Inverse Momentum ETF (Bearish)
========================================================================
LÓGICA:
- Trackers de SEÑAL: QQQ y SPY (barras del WebSocket).
- ENTRADA LONG del ETF INVERSO correspondiente (SQQQ ← QQQ, SPXU ← SPY)
  cuando el tracker cumple:
    * MACD(12,26,9) histograma < 0  (momentum bajista)
    * close < SMA200                (tendencia larga bajista)
    * No hay posición ya abierta en el ETF inverso
- SALIDA del ETF inverso:
    * macd_histogram >= 0 (la tendencia se revierte), OR
    * Stop Loss  -2% desde el precio de entrada del inverso, OR
    * Take Profit +3% desde el precio de entrada del inverso

Nota: los inversos SQQQ/SPXU se mueven inversamente (y apalancados) respecto
al tracker. Usamos la cotización del tracker como proxy direccional para
Stop/Take: una subida del tracker implica pérdida en el inverso.
"""
import logging
import pandas as pd
from collections import deque
from ta.trend import MACD
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class InverseMomentumETF(BaseStrategy):

    STRAT_NUMBER = 11

    # Tracker base -> ETF inverso que realmente se opera (long del inverso)
    BASE_TO_INVERSE = {
        "QQQ": "SQQQ",
        "SPY": "SPXU",
    }

    FAST_EMA = 12
    SLOW_EMA = 26
    SIGNAL_EMA = 9
    SMA_SLOW = 200

    STOP_LOSS_PCT   = 0.02   # -2% en el ETF inverso (≈ +~0.66% en tracker si 3x)
    TAKE_PROFIT_PCT = 0.03   # +3% en el ETF inverso

    HEARTBEAT_EVERY = 10     # log informativo cada N barras por tracker

    def __init__(self, order_manager, regime_manager=None):
        super().__init__(
            name="InverseMomentumETF",
            symbols=list(self.BASE_TO_INVERSE.keys()),
            order_manager=order_manager,
        )
        self.regime_manager = regime_manager

        # Histórico de cierres por tracker
        self._closes = {sym: deque(maxlen=self.SMA_SLOW + 5) for sym in self.symbols}
        self._bar_count = {sym: 0 for sym in self.symbols}

        # Estado de posición por ETF INVERSO
        self._has_position: dict = {}
        self._qty_bought: dict = {}
        self._entry_price_base: dict = {}   # precio del tracker al entrar (proxy)
        for inverse_sym in self.BASE_TO_INVERSE.values():
            qty = self.sync_position_from_alpaca(inverse_sym)
            self._has_position[inverse_sym] = qty > 0
            self._qty_bought[inverse_sym] = qty
            self._entry_price_base[inverse_sym] = 0.0

    async def on_bar(self, bar) -> None:
        sym = bar.symbol
        if not self.should_process(sym):
            return
        if sym not in self.BASE_TO_INVERSE:
            return

        self._closes[sym].append(float(bar.close))
        self._bar_count[sym] += 1

        if len(self._closes[sym]) < self.SMA_SLOW:
            return

        s = pd.Series(list(self._closes[sym]))

        # MACD(12, 26, 9)
        macd_ind = MACD(
            close=s,
            window_fast=self.FAST_EMA,
            window_slow=self.SLOW_EMA,
            window_sign=self.SIGNAL_EMA,
        )
        macd_hist = macd_ind.macd_diff().iloc[-1]

        # SMA200
        sma200 = s.rolling(window=self.SMA_SLOW).mean().iloc[-1]

        if pd.isna(macd_hist) or pd.isna(sma200):
            return

        curr_price = float(bar.close)
        inverse_sym = self.BASE_TO_INVERSE[sym]
        has_pos = self._has_position.get(inverse_sym, False)

        # Heartbeat cada HEARTBEAT_EVERY barras
        if self._bar_count[sym] % self.HEARTBEAT_EVERY == 0:
            logger.info(
                f"[{self.name}] Evaluando {sym}: MACD_hist={macd_hist:.4f} "
                f"close={curr_price:.2f} SMA200={sma200:.2f} -> esperando condicion "
                f"(pos {inverse_sym}={'SI' if has_pos else 'NO'})"
            )

        # ======================= SALIDA =======================
        if has_pos:
            entry_base = self._entry_price_base.get(inverse_sym, 0.0)
            reason = None

            if macd_hist >= 0:
                reason = f"MACD_hist {macd_hist:.4f} >= 0 (reversion)"
            elif entry_base > 0:
                # Proxy por tracker: el inverso pierde ~pct cuando el tracker sube ~pct
                # (para 3x multiplicar por 3; aquí usamos 1:1 como aproximación conservadora)
                change_base = (curr_price - entry_base) / entry_base
                # Inverso: pnl_pct ≈ -change_base
                inverse_pnl = -change_base
                if inverse_pnl <= -self.STOP_LOSS_PCT:
                    reason = f"Stop Loss {inverse_pnl*100:.2f}% <= -{self.STOP_LOSS_PCT*100:.0f}%"
                elif inverse_pnl >= self.TAKE_PROFIT_PCT:
                    reason = f"Take Profit {inverse_pnl*100:.2f}% >= +{self.TAKE_PROFIT_PCT*100:.0f}%"

            if reason:
                logger.info(
                    f"[{self.name}] SALIDA {inverse_sym}: {reason}. VENDIENDO {inverse_sym}"
                )
                qty = self._qty_bought.get(inverse_sym, 0.0)
                if hasattr(self.order_manager, "sell_exact") and qty > 0:
                    await self.order_manager.sell_exact(
                        inverse_sym, qty, strategy_name=self.name
                    )
                else:
                    await self.order_manager.sell(
                        inverse_sym, strategy_name=self.name
                    )
                self._has_position[inverse_sym] = False
                self._qty_bought[inverse_sym] = 0.0
                self._entry_price_base[inverse_sym] = 0.0
                self._position[inverse_sym] = 0
                return

        # ======================= ENTRADA ======================
        if not has_pos and macd_hist < 0 and curr_price < sma200:
            logger.info(
                f"[{self.name}] Senal BAJISTA en {sym} (MACD_hist={macd_hist:.4f} < 0, "
                f"close={curr_price:.2f} < SMA200={sma200:.2f}). COMPRANDO inverso {inverse_sym}"
            )

            # Soft Guard: régimen ETF debe permitir la estrategia
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(
                self.STRAT_NUMBER, engine="etf"
            ):
                logger.info(
                    f"[{self.name}] Guard regime: strat {self.STRAT_NUMBER} deshabilitada en regimen actual. Skip."
                )
                return

            # Sizing dinámico delegado a OrderManager.buy()
            await self.order_manager.buy(inverse_sym, strategy_name=self.name)
            self._has_position[inverse_sym] = True
            self._entry_price_base[inverse_sym] = curr_price
            self._position[inverse_sym] = 1

            # Intento best-effort de capturar qty recién comprada para sell_exact
            try:
                qty_synced = self.sync_position_from_alpaca(inverse_sym)
                if qty_synced > 0:
                    self._qty_bought[inverse_sym] = qty_synced
            except Exception:
                pass
