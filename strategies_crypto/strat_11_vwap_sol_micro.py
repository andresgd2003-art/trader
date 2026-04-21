import logging
from datetime import datetime, timedelta
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoMicroVWAPSolStrategy(BaseStrategy):
    """
    11 - Micro-VWAP Scalper SOL/USD

    Intraday scalper for SOL/USD based on deviation from the daily VWAP
    (Volume-Weighted Average Price), reset every day at 00:00 UTC.

    Entry logic — two $15 "bullets" fired on VWAP dips:
      - Bullet 1: price is -0.4% (or more) below VWAP  → buy $15
      - Bullet 2: price drops further to -0.8% below VWAP → buy another $15

    Exit logic (applies to the total accumulated position):
      - VWAP Touch: price climbs back up to VWAP (deviation >= 0%) → full sell
      - Stop Loss:  price drops to -2.0% below VWAP              → full sell

    After a stop-loss exit a 15-minute cooldown prevents re-entry.

    ⚠️ SOL/USD is shared with the Grid Spot strategy (strat_06).  The arbiter
    (OrderManager) serialises access.  On start-up any existing SOL position
    is intentionally left untouched — the scalper begins with _bullets_fired=0
    and will only manage positions it personally initiates.
    """

    # ------------------------------------------------------------------ #
    #  Constants                                                           #
    # ------------------------------------------------------------------ #
    SYMBOL                 = "SOL/USD"
    BULLET_USD             = 15.0       # Each bullet = $15 (= DAY_CAP_USD of OrderManager)
    ENTRY_1_DEV            = -0.004     # -0.4% below VWAP
    ENTRY_2_DEV            = -0.008     # -0.8% below VWAP
    EXIT_DEV               = 0.0        # Price >= VWAP (touch from below)
    STOP_LOSS_DEV          = -0.020     # -2.0% panic exit
    COOLDOWN_SECS_AFTER_SL = 900        # 15 min after stop loss
    ARBITER_PRIORITY       = 5          # P5 — intraday scalp
    STRAT_NUMBER           = 11         # For regime_manager.is_strategy_enabled()

    # ------------------------------------------------------------------ #
    #  Constructor                                                         #
    # ------------------------------------------------------------------ #
    def __init__(self, order_manager, regime_manager=None):
        super().__init__("Micro-VWAP Scalper SOL", [self.SYMBOL], order_manager)
        self.regime_manager = regime_manager

        # VWAP state — reset daily at UTC midnight
        self._vwap_sum_pv   = 0.0
        self._vwap_sum_v    = 0.0
        self._current_vwap  = 0.0
        self._last_reset_day = -1

        # Trade state
        self._bullets_fired    = 0
        self._bullet_qty_total = 0.0
        self._cooldown_until   = None

        # Anti-duplication: check for an existing SOL position on startup.
        # Grid Spot (strat_06) also trades SOL/USD, so a pre-existing position
        # almost certainly belongs to it.  The scalper must NOT claim it.
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        if qty > 0:
            logger.warning(
                f"[{self.name}] Posición SOL existente detectada al arrancar "
                f"(probablemente de Grid Spot). Scalper NO la reclamará. "
                f"_bullets_fired=0"
            )
        # _bullets_fired stays 0 regardless — scalper starts clean.

    # ------------------------------------------------------------------ #
    #  Main bar handler                                                    #
    # ------------------------------------------------------------------ #
    async def on_bar(self, bar):

        # ── 1. Early-exit checks ──────────────────────────────────────── #
        if not self.should_process(bar.symbol):
            return
        if (self.regime_manager and
                not self.regime_manager.is_strategy_enabled(
                    self.STRAT_NUMBER, engine="crypto")):
            return

        # ── 2. VWAP daily reset at UTC midnight ──────────────────────── #
        dt = bar.timestamp
        if dt.day != self._last_reset_day:
            self._last_reset_day = dt.day
            self._vwap_sum_pv = 0.0
            self._vwap_sum_v  = 0.0
            logger.info(f"[{self.name}] VWAP reseteado para día UTC {dt.day}")

        # ── 3. Accumulate VWAP ───────────────────────────────────────── #
        typical_price = (bar.high + bar.low + bar.close) / 3
        self._vwap_sum_pv += typical_price * bar.volume
        self._vwap_sum_v  += bar.volume
        if self._vwap_sum_v <= 0:
            return
        self._current_vwap = self._vwap_sum_pv / self._vwap_sum_v

        # ── 4. Deviation from VWAP ───────────────────────────────────── #
        deviation = (float(bar.close) - self._current_vwap) / self._current_vwap

        # ── 5. Exit path ─────────────────────────────────────────────── #
        if self._bullets_fired > 0:
            hit_exit      = deviation >= self.EXIT_DEV
            hit_stop_loss = deviation <= self.STOP_LOSS_DEV

            if hit_exit or hit_stop_loss:
                reason = "VWAP Touch" if hit_exit else "Stop Loss -2%"

                # Safety check: Grid Spot may have sold SOL underneath us.
                real_qty = self.sync_position_from_alpaca(self.SYMBOL)
                if real_qty < self._bullet_qty_total:
                    logger.warning(
                        f"[{self.name}] real_qty ({real_qty:.4f}) < "
                        f"_bullet_qty_total ({self._bullet_qty_total:.4f}). "
                        f"Grid Spot probablemente vendió. Ajustando exit qty."
                    )
                    exact_qty = real_qty
                else:
                    exact_qty = self._bullet_qty_total

                logger.info(
                    f"[{self.name}] Saliendo por '{reason}'. "
                    f"Precio={float(bar.close):.2f} VWAP={self._current_vwap:.2f} "
                    f"dev={deviation*100:+.2f}% qty={exact_qty:.4f} SOL"
                )
                if exact_qty > 0:
                    await self.order_manager.sell_exact(
                        symbol=self.SYMBOL,
                        exact_qty=exact_qty,
                        strategy_name=self.name
                    )
                else:
                    logger.warning(
                        f"[{self.name}] exact_qty=0 — Grid Spot ya vendió todo. Skip sell_exact, limpiando estado interno."
                    )

                if hit_stop_loss:
                    self._cooldown_until = (
                        datetime.utcnow()
                        + timedelta(seconds=self.COOLDOWN_SECS_AFTER_SL)
                    )
                    logger.info(
                        f"[{self.name}] Stop Loss activado. "
                        f"Cooldown hasta {self._cooldown_until.strftime('%H:%M:%S')} UTC"
                    )

                self.order_manager.release_asset(self.SYMBOL, self.name)
                self._bullets_fired    = 0
                self._bullet_qty_total = 0.0
                return

        # ── 6. Entry path ────────────────────────────────────────────── #

        # Cooldown guard (only relevant after a stop loss)
        if self._cooldown_until and datetime.utcnow() < self._cooldown_until:
            return

        # Bullet 1 — first entry at -0.4% below VWAP
        if self._bullets_fired == 0 and deviation <= self.ENTRY_1_DEV:
            granted = await self.order_manager.request_buy(
                symbol=self.SYMBOL,
                priority=self.ARBITER_PRIORITY,
                strategy_name=self.name
            )
            if not granted:
                logger.debug(
                    f"[{self.name}] Arbiter denied SOL lock, "
                    f"probably Grid Spot is holding"
                )
                return

            await self.order_manager.buy(
                symbol=self.SYMBOL,
                notional_usd=self.BULLET_USD,
                current_price=float(bar.close),
                strategy_name=self.name
            )
            qty_added = round(self.BULLET_USD / float(bar.close), 4)
            self._bullet_qty_total += qty_added
            self._bullets_fired = 1
            logger.info(
                f"[{self.name}] Bala 1 disparada @ ${float(bar.close):.2f} "
                f"(dev {deviation*100:+.2f}%). Qty: {qty_added} SOL"
            )

        # Bullet 2 — scale-in at -0.8% below VWAP (arbiter lock already held)
        elif self._bullets_fired == 1 and deviation <= self.ENTRY_2_DEV:
            await self.order_manager.buy(
                symbol=self.SYMBOL,
                notional_usd=self.BULLET_USD,
                current_price=float(bar.close),
                strategy_name=self.name
            )
            qty_added = round(self.BULLET_USD / float(bar.close), 4)
            self._bullet_qty_total += qty_added
            self._bullets_fired = 2
            logger.info(
                f"[{self.name}] Bala 2 disparada @ ${float(bar.close):.2f} "
                f"(dev {deviation*100:+.2f}%). Total qty: {self._bullet_qty_total} SOL"
            )

        # ── 7. Telemetry ─────────────────────────────────────────────── #
        logger.debug(
            f"[{self.name}] Price={float(bar.close):.2f} "
            f"VWAP={self._current_vwap:.2f} "
            f"dev={deviation*100:+.2f}% "
            f"bullets={self._bullets_fired}"
        )
