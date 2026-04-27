import logging
from datetime import datetime, timedelta
from engine.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class CryptoMicroVWAPAvaxStrategy(BaseStrategy):
    """
    11 - Micro-VWAP Aggressive Scalper AVAX/USD

    Intraday scalper that targets AVAX/USD — deliberately separated from
    SOL/USD (Grid Spot) to avoid the arbiter deadlock that killed the
    previous SOL-based implementation.

    Entry logic — two $15 bullets on VWAP dips:
      - Bullet 1: price ≤ -0.5% below VWAP → buy $15
      - Bullet 2: price ≤ -1.0% below VWAP → buy another $15
    Exit logic (applies to accumulated position):
      - VWAP Touch: price ≥ VWAP → full sell
      - Stop Loss:  price ≤ -2.5% below VWAP → full sell
    Cooldown 15 min after stop loss.

    Design notes vs previous SOL version:
      - Operates on an asset nobody else uses → no arbiter contention.
      - Tracks its OWN bought qty (_bullet_qty_total) and sells exact qty.
      - NO request_buy/release_asset calls: AVAX has no competing strategy,
        so arbiter locks are unnecessary overhead. Entries call buy() direct.
    """

    SYMBOL                 = "AVAX/USD"
    BULLET_USD             = 15.0
    ENTRY_1_DEV            = -0.005
    ENTRY_2_DEV            = -0.010
    EXIT_DEV               = 0.0
    STOP_LOSS_DEV          = -0.025
    COOLDOWN_SECS_AFTER_SL = 900
    STRAT_NUMBER           = 11
    FORCED_STOP_LOSS_PCT   = 0.04   # -4%
    FORCED_TAKE_PROFIT_PCT = 0.06   # +6%

    def __init__(self, order_manager, regime_manager=None):
        super().__init__("Micro-VWAP AVAX Aggressive", [self.SYMBOL], order_manager)
        self.regime_manager = regime_manager
        self._vwap_sum_pv    = 0.0
        self._vwap_sum_v     = 0.0
        self._current_vwap   = 0.0
        self._last_reset_day = -1
        self._bullets_fired    = 0
        self._bullet_qty_total = 0.0
        self._cooldown_until   = None
        self._entry_price = 0.0
        qty = self.sync_position_from_alpaca(self.SYMBOL)
        if qty > 0:
            logger.info(f"[{self.name}] Posición AVAX existente detectada qty={qty}. Adoptando como propia.")
            self._bullet_qty_total = qty
            self._bullets_fired = 1
            try:
                pos = self.order_manager.broker.get_position(self.SYMBOL) if hasattr(self.order_manager, "broker") else None
                if pos:
                    self._entry_price = float(pos.avg_entry_price)
            except Exception:
                pass

    async def on_bar(self, bar):
        if not self.should_process(bar.symbol):
            return


        dt = bar.timestamp
        if dt.day != self._last_reset_day:
            self._last_reset_day = dt.day
            self._vwap_sum_pv = 0.0
            self._vwap_sum_v  = 0.0
            logger.info(f"[{self.name}] VWAP reseteado para día UTC {dt.day}")

        typical_price = (bar.high + bar.low + bar.close) / 3
        self._vwap_sum_pv += typical_price * bar.volume
        self._vwap_sum_v  += bar.volume
        if self._vwap_sum_v <= 0:
            return
        self._current_vwap = self._vwap_sum_pv / self._vwap_sum_v

        deviation = (float(bar.close) - self._current_vwap) / self._current_vwap

        # === FORCED EXIT (SL -4% / TP +6%) por entry_price tracking ===
        if self._bullets_fired > 0 and self._entry_price > 0 and self._bullet_qty_total > 0:
            pnl_pct = (float(bar.close) - self._entry_price) / self._entry_price
            if pnl_pct <= -self.FORCED_STOP_LOSS_PCT or pnl_pct >= self.FORCED_TAKE_PROFIT_PCT:
                tag = "SL -4%" if pnl_pct <= -self.FORCED_STOP_LOSS_PCT else "TP +6%"
                logger.warning(f"[{self.name}] FORCED {tag} en AVAX. entry={self._entry_price:.4f} now={float(bar.close):.4f}")
                real_qty = self.sync_position_from_alpaca(self.SYMBOL)
                exact_qty = min(real_qty, self._bullet_qty_total) if real_qty > 0 else 0
                if exact_qty > 0:
                    await self.order_manager.sell_exact(
                        symbol=self.SYMBOL, exact_qty=exact_qty, strategy_name=self.name
                    )
                if pnl_pct <= -self.FORCED_STOP_LOSS_PCT:
                    self._cooldown_until = datetime.utcnow() + timedelta(seconds=self.COOLDOWN_SECS_AFTER_SL)
                self._bullets_fired = 0
                self._bullet_qty_total = 0.0
                self._entry_price = 0.0
                return

        # Exit path
        if self._bullets_fired > 0:
            hit_exit      = deviation >= self.EXIT_DEV
            hit_stop_loss = deviation <= self.STOP_LOSS_DEV
            if hit_exit or hit_stop_loss:
                reason = "VWAP Touch" if hit_exit else "Stop Loss -2.5%"
                real_qty = self.sync_position_from_alpaca(self.SYMBOL)
                exact_qty = min(real_qty, self._bullet_qty_total) if real_qty > 0 else 0
                logger.info(
                    f"[{self.name}] Saliendo por '{reason}'. "
                    f"Precio={float(bar.close):.4f} VWAP={self._current_vwap:.4f} "
                    f"dev={deviation*100:+.2f}% qty={exact_qty:.4f} AVAX"
                )
                if exact_qty > 0:
                    await self.order_manager.sell_exact(
                        symbol=self.SYMBOL,
                        exact_qty=exact_qty,
                        strategy_name=self.name
                    )
                if hit_stop_loss:
                    self._cooldown_until = datetime.utcnow() + timedelta(seconds=self.COOLDOWN_SECS_AFTER_SL)
                    logger.info(f"[{self.name}] Stop Loss. Cooldown hasta {self._cooldown_until.strftime('%H:%M:%S')} UTC")
                self._bullets_fired    = 0
                self._bullet_qty_total = 0.0
                self._entry_price      = 0.0
                return

        # Entry path
        if self._cooldown_until and datetime.utcnow() < self._cooldown_until:
            return

        if self._bullets_fired == 0 and deviation <= self.ENTRY_1_DEV:
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
            await self.order_manager.buy(
                symbol=self.SYMBOL,
                notional_usd=self.BULLET_USD,
                current_price=float(bar.close),
                strategy_name=self.name
            )
            qty_added = round(self.BULLET_USD / float(bar.close), 4)
            self._bullet_qty_total += qty_added
            self._bullets_fired = 1
            self._entry_price = float(bar.close)
            logger.info(f"[{self.name}] Bala 1 @ ${float(bar.close):.4f} (dev {deviation*100:+.2f}%) qty={qty_added} AVAX")

        elif self._bullets_fired == 1 and deviation <= self.ENTRY_2_DEV:
            if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="crypto"): return
            await self.order_manager.buy(
                symbol=self.SYMBOL,
                notional_usd=self.BULLET_USD,
                current_price=float(bar.close),
                strategy_name=self.name
            )
            qty_added = round(self.BULLET_USD / float(bar.close), 4)
            # avg ponderado de entry tras Bala 2
            prev_qty = self._bullet_qty_total
            if (prev_qty + qty_added) > 0:
                self._entry_price = (
                    (self._entry_price * prev_qty) + (float(bar.close) * qty_added)
                ) / (prev_qty + qty_added)
            self._bullet_qty_total += qty_added
            self._bullets_fired = 2
            logger.info(f"[{self.name}] Bala 2 @ ${float(bar.close):.4f} (dev {deviation*100:+.2f}%) total qty={self._bullet_qty_total} AVAX")
