"""
backtest/backtest_new_strategies.py
====================================
Backtest standalone de las 3 nuevas estrategias usando datos diarios de yfinance.
Periodos: 2020 (COVID crash) y 2022 (bear market).
Portfolio inicial: $200. Slippage: 0.1% por trade.

FIX KEY: Se descargan 300 dias EXTRA de warmup antes del periodo para que
SMA200 y MACD tengan suficiente historia al inicio del periodo real.

Ejecucion:
    venv/Scripts/python.exe backtest/backtest_new_strategies.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
import warnings
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

OUT_DIR = os.path.join(os.path.dirname(__file__), "equity_curves")
os.makedirs(OUT_DIR, exist_ok=True)

SLIPPAGE     = 0.001   # 0.1% por trade
INITIAL_EQUITY = 200.0
WARMUP_DAYS  = 300     # dias extra antes del periodo para calcular SMA200

PERIODS = {
    "2020": ("2020-01-01", "2020-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
}

# ─── helpers ──────────────────────────────────────────────────────────────────

def warmup_start(start_str):
    """Resta WARMUP_DAYS al inicio del periodo."""
    d = datetime.strptime(start_str, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)
    return d.strftime("%Y-%m-%d")


def download(tickers, start, end):
    """Descarga OHLC diario. Retorna dict {ticker: Series Close}."""
    data = {}
    for t in tickers:
        try:
            ticker = yf.Ticker(t)
            df = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)
            if df.empty:
                print(f"  [WARN] Sin datos para {t}")
            else:
                col = df["Close"]
                if isinstance(col, pd.DataFrame):
                    col = col.iloc[:, 0]
                # Remove timezone for consistent indexing
                col.index = col.index.tz_localize(None) if col.index.tz else col.index
                data[t] = col.squeeze()
        except Exception as e:
            print(f"  [WARN] Error descargando {t}: {e}")
    return data


def clip_to_period(series, start, end):
    """Recorta la serie al periodo real (post-warmup)."""
    return series.loc[start:end]


def rsi_series(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd_hist_series(close, fast=12, slow=26, signal=9):
    ema_f  = close.ewm(span=fast,   adjust=False).mean()
    ema_s  = close.ewm(span=slow,   adjust=False).mean()
    line   = ema_f - ema_s
    sig    = line.ewm(span=signal,  adjust=False).mean()
    return line - sig


def regime_series(spy_close, vix_close):
    """BULL/BEAR/CHOP usando SMA200 + VIX sobre la serie COMPLETA (incluye warmup)."""
    sma200 = spy_close.rolling(200).mean()
    reg    = pd.Series("CHOP", index=spy_close.index, dtype=str)
    bull   = (spy_close > sma200) & (vix_close < 20)
    bear   = (spy_close < sma200) & (vix_close > 22)
    reg[bull] = "BULL"
    reg[bear] = "BEAR"
    return reg


def trade_stats(trades, period_start, period_end):
    if not trades:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "total_pnl": 0.0, "total_pnl_pct": 0.0,
                "max_dd": 0.0, "sharpe": None, "best": 0.0, "worst": 0.0}
    pnls   = [t["pnl"] for t in trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    equity = np.cumsum(pnls) + INITIAL_EQUITY
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / peak
    daily  = pd.Series(pnls) / INITIAL_EQUITY
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)
              if daily.std() > 0 else None)
    return {
        "trades":        len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      len(wins) / len(trades) if trades else None,
        "total_pnl":     round(sum(pnls), 4),
        "total_pnl_pct": round(sum(pnls) / INITIAL_EQUITY * 100, 2),
        "max_dd":        round(float(dd.min()) * 100, 2),
        "sharpe":        round(float(sharpe), 3) if sharpe else None,
        "best":          round(max(pnls), 4),
        "worst":         round(min(pnls), 4),
    }


# ─── Strategy 1: InverseMomentumETF ──────────────────────────────────────────

def backtest_inverse_momentum(data, reg, period_start, period_end):
    """
    Trackers QQQ->SQQQ, SPY->SPXU.
    Entry:  MACD_hist < 0 AND close < SMA200 AND regime in (BEAR, CHOP)
    Exit:   MACD_hist >= 0 OR SL -2% OR TP +3% (proxy via tracker)
    """
    BASE_TO_INVERSE = {"QQQ": "SQQQ", "SPY": "SPXU"}
    SMA_PERIOD  = 200
    SL          = 0.02
    TP          = 0.03
    SIZING_PCT  = 0.06   # 6% per trade

    trades = []
    equity = INITIAL_EQUITY

    for tracker, inverse in BASE_TO_INVERSE.items():
        if tracker not in data or inverse not in data:
            print(f"    [WARN] Sin datos para {tracker}/{inverse}")
            continue

        ct_full  = data[tracker]
        ci_full  = data[inverse]

        # Calculate indicators on full series (warmup included) for accuracy
        sma200   = ct_full.rolling(SMA_PERIOD).mean()
        mhist    = macd_hist_series(ct_full)

        # Clip to evaluation period only
        ct_eval  = clip_to_period(ct_full,  period_start, period_end)
        ci_eval  = clip_to_period(ci_full,  period_start, period_end).reindex(ct_eval.index).ffill()
        sma_eval = clip_to_period(sma200,   period_start, period_end)
        mh_eval  = clip_to_period(mhist,    period_start, period_end)
        reg_eval = reg.reindex(ct_eval.index, method="ffill")

        pos = None

        for i, date in enumerate(ct_eval.index):
            ct  = float(ct_eval.iloc[i])
            ci  = float(ci_eval.iloc[i])  if not pd.isna(ci_eval.iloc[i])  else 0
            mh  = float(mh_eval.iloc[i])  if not pd.isna(mh_eval.iloc[i])  else 0
            sma = float(sma_eval.iloc[i]) if not pd.isna(sma_eval.iloc[i]) else ct
            r   = str(reg_eval.iloc[i])

            if ci <= 0:
                continue

            # EXIT
            if pos is not None:
                change_base = (ct - pos["entry_base"]) / pos["entry_base"]
                inv_pnl     = -change_base   # inverse correlates negatively to tracker

                reason = None
                if mh >= 0:
                    reason = "MACD>=0"
                elif inv_pnl <= -SL:
                    reason = "StopLoss"
                elif inv_pnl >= TP:
                    reason = "TakeProfit"

                if reason:
                    exit_px = ci * (1 - SLIPPAGE)
                    pnl = (exit_px - pos["entry_inv"]) * pos["qty"]
                    equity += pnl
                    trades.append({"date": str(date.date()), "sym": inverse,
                                   "reason": reason, "pnl": round(pnl, 4)})
                    pos = None
                continue

            # ENTRY
            if mh < 0 and ct < sma and r in ("BEAR", "CHOP"):
                invest   = equity * SIZING_PCT
                buy_px   = ci * (1 + SLIPPAGE)
                qty      = invest / buy_px if buy_px > 0 else 0
                if qty > 0:
                    pos = {"entry_base": ct, "entry_inv": buy_px, "qty": qty}

        if pos is not None:
            ci_last = float(ci_eval.iloc[-1])
            pnl = (ci_last * (1 - SLIPPAGE) - pos["entry_inv"]) * pos["qty"]
            equity += pnl
            trades.append({"sym": inverse, "reason": "EndOfPeriod", "pnl": round(pnl, 4)})

    return trades


# ─── Strategy 2: DefensiveRotation ───────────────────────────────────────────

def backtest_defensive_rotation(data, reg, period_start, period_end):
    """
    Tracker SPY RSI(14). Universe KO/PG/JNJ/WMT/PEP.
    Entry:  regime BEAR/CHOP AND RSI_SPY < 40 -> comprar ticker con RSI mas bajo
    Exit:   RSI_SPY > 55 OR TP +2% OR regime BULL
    """
    UNIVERSE   = ["KO", "PG", "JNJ", "WMT", "PEP"]
    TP         = 0.02
    RSI_BUY    = 40
    RSI_EXIT   = 55
    RSI_PERIOD = 14
    SIZING_PCT = 0.10

    if "SPY" not in data:
        print("    [WARN] Sin SPY para DefensiveRotation")
        return []

    spy_full  = data["SPY"]
    spy_rsi_f = rsi_series(spy_full, RSI_PERIOD)

    spy_eval  = clip_to_period(spy_full,   period_start, period_end)
    spy_rsi   = clip_to_period(spy_rsi_f,  period_start, period_end)
    reg_eval  = reg.reindex(spy_eval.index, method="ffill")

    # Pre-calculate RSI for universe on full data (warmup included)
    uni_rsi = {}
    for sym in UNIVERSE:
        if sym in data:
            uni_rsi[sym] = clip_to_period(rsi_series(data[sym], RSI_PERIOD), period_start, period_end)

    trades   = []
    equity   = INITIAL_EQUITY
    position = None

    for i, date in enumerate(spy_eval.index):
        r     = str(reg_eval.iloc[i])
        spy_r = float(spy_rsi.iloc[i]) if not pd.isna(spy_rsi.iloc[i]) else 50.0

        # EXIT
        if position is not None:
            sym = position["sym"]
            if sym in data:
                price_s = clip_to_period(data[sym], period_start, period_end)
                if date in price_s.index:
                    curr    = float(price_s.loc[date])
                    tp_hit  = (curr - position["entry"]) / position["entry"] >= TP
                    exit_ok = spy_r > RSI_EXIT or r == "BULL" or tp_hit
                    if exit_ok:
                        pnl = (curr * (1 - SLIPPAGE) - position["entry"]) * position["qty"]
                        equity += pnl
                        reason = "TP" if tp_hit else ("BullRegime" if r == "BULL" else "RSI_exit")
                        trades.append({"date": str(date.date()), "sym": sym,
                                       "reason": reason, "pnl": round(pnl, 4)})
                        position = None

        # ENTRY
        if position is None and spy_r < RSI_BUY and r in ("BEAR", "CHOP"):
            best     = None
            best_val = 100.0
            for sym, rsi_ser in uni_rsi.items():
                val = float(rsi_ser.loc[date]) if date in rsi_ser.index and not pd.isna(rsi_ser.loc[date]) else 100.0
                if val < best_val:
                    best_val = val
                    best = sym

            if best and best in data:
                price_s = clip_to_period(data[best], period_start, period_end)
                if date in price_s.index:
                    px      = float(price_s.loc[date])
                    buy_px  = px * (1 + SLIPPAGE)
                    invest  = equity * SIZING_PCT
                    qty     = invest / buy_px if buy_px > 0 else 0
                    if qty > 0:
                        position = {"sym": best, "entry": buy_px, "qty": qty}

    if position is not None:
        sym = position["sym"]
        if sym in data:
            last = float(clip_to_period(data[sym], period_start, period_end).iloc[-1])
            pnl  = (last * (1 - SLIPPAGE) - position["entry"]) * position["qty"]
            equity += pnl
            trades.append({"sym": sym, "reason": "EndOfPeriod", "pnl": round(pnl, 4)})

    return trades


# ─── Strategy 3: CryptoMeanReversionExtreme ──────────────────────────────────

def backtest_crypto_mean_reversion(data, period_start, period_end):
    """
    BTC-USD, ETH-USD.
    Entry: close < BB_lower(20, 2.5) AND RSI < 25
    Exit:  close >= BB_middle OR RSI > 50 OR SL -3%
    """
    SYMBOLS      = ["BTC-USD", "ETH-USD"]
    BB_WINDOW    = 20
    BB_STD       = 2.5
    RSI_OVERSOLD = 25
    RSI_EXIT     = 50
    SL           = 0.03
    SIZING_PCT   = 0.10

    trades = []
    equity = INITIAL_EQUITY

    for sym in SYMBOLS:
        if sym not in data:
            print(f"    [WARN] Sin datos para {sym}")
            continue

        close_full  = data[sym]

        # Indicators on full series for warmup accuracy
        bb_mid_f    = close_full.rolling(BB_WINDOW).mean()
        bb_std_f    = close_full.rolling(BB_WINDOW).std()
        bb_lower_f  = bb_mid_f - BB_STD * bb_std_f
        rsi_f       = rsi_series(close_full)

        close_eval  = clip_to_period(close_full,  period_start, period_end)
        bm_eval     = clip_to_period(bb_mid_f,    period_start, period_end)
        bl_eval     = clip_to_period(bb_lower_f,  period_start, period_end)
        rsi_eval    = clip_to_period(rsi_f,        period_start, period_end)

        pos = None

        for i, date in enumerate(close_eval.index):
            c  = float(close_eval.iloc[i])
            bm = float(bm_eval.iloc[i])  if not pd.isna(bm_eval.iloc[i])  else c
            bl = float(bl_eval.iloc[i])  if not pd.isna(bl_eval.iloc[i])  else c
            r  = float(rsi_eval.iloc[i]) if not pd.isna(rsi_eval.iloc[i]) else 50

            if pos is not None:
                change = (c - pos["entry"]) / pos["entry"]
                if c >= bm or r > RSI_EXIT or change <= -SL:
                    exit_px = c * (1 - SLIPPAGE)
                    pnl     = (exit_px - pos["entry"]) * pos["qty"]
                    equity += pnl
                    reason  = "SL" if change <= -SL else ("BBmid" if c >= bm else "RSI>50")
                    trades.append({"date": str(date.date()), "sym": sym,
                                   "reason": reason, "pnl": round(pnl, 4)})
                    pos = None
                continue

            if c < bl and r < RSI_OVERSOLD:
                invest = equity * SIZING_PCT
                buy_px = c * (1 + SLIPPAGE)
                qty    = invest / buy_px if buy_px > 0 else 0
                if qty > 0:
                    pos = {"entry": buy_px, "qty": qty}

        if pos is not None:
            last_px = float(close_eval.iloc[-1]) * (1 - SLIPPAGE)
            pnl     = (last_px - pos["entry"]) * pos["qty"]
            equity += pnl
            trades.append({"sym": sym, "reason": "EndOfPeriod", "pnl": round(pnl, 4)})

    return trades


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_period(year, period_start, period_end):
    print(f"\n{'='*60}")
    print(f"  PERIODO: {year}  ({period_start} -> {period_end})")
    print(f"  Warmup:  {warmup_start(period_start)} -> {period_start} ({WARMUP_DAYS} dias extra)")
    print(f"{'='*60}")

    dl_start = warmup_start(period_start)

    etf_tickers    = ["SPY", "QQQ", "SQQQ", "SPXU"]
    eq_tickers     = ["SPY", "KO", "PG", "JNJ", "WMT", "PEP"]
    crypto_tickers = ["BTC-USD", "ETH-USD"]

    print("  Descargando datos (con warmup)...")
    etf_data    = download(etf_tickers,    dl_start, period_end)
    eq_data     = download(eq_tickers,     dl_start, period_end)
    crypto_data = download(crypto_tickers, dl_start, period_end)
    vix_data    = download(["^VIX"],       dl_start, period_end)

    # Regime on full dataset (post-warmup will have SMA200 ready)
    if "SPY" in etf_data and "^VIX" in vix_data:
        spy_full = etf_data["SPY"]
        vix_full = vix_data["^VIX"].reindex(spy_full.index).ffill().bfill()
        reg_full = regime_series(spy_full, vix_full)
    else:
        print("  [ERROR] Sin SPY/VIX — usando CHOP")
        spy_full = etf_data.get("SPY", pd.Series())
        reg_full = pd.Series("CHOP", index=spy_full.index, dtype=str)

    # Show regime distribution in the eval period
    reg_eval = clip_to_period(reg_full, period_start, period_end)
    counts   = reg_eval.value_counts().to_dict()
    print(f"  Regimen en {year}: {counts}")

    results = {}

    print(f"\n  [1/3] InverseMomentumETF...")
    t1 = backtest_inverse_momentum(etf_data, reg_full, period_start, period_end)
    results["InverseMomentumETF"] = trade_stats(t1, period_start, period_end)
    s1 = results["InverseMomentumETF"]
    print(f"    Trades={s1['trades']}  Wins={s1['wins']}  PnL=${s1['total_pnl']} ({s1['total_pnl_pct']}%)")

    print(f"  [2/3] DefensiveRotation...")
    t2 = backtest_defensive_rotation(eq_data, reg_full, period_start, period_end)
    results["DefensiveRotation"] = trade_stats(t2, period_start, period_end)
    s2 = results["DefensiveRotation"]
    print(f"    Trades={s2['trades']}  Wins={s2['wins']}  PnL=${s2['total_pnl']} ({s2['total_pnl_pct']}%)")

    print(f"  [3/3] CryptoMeanReversionExtreme...")
    t3 = backtest_crypto_mean_reversion(crypto_data, period_start, period_end)
    results["CryptoMeanReversionExtreme"] = trade_stats(t3, period_start, period_end)
    s3 = results["CryptoMeanReversionExtreme"]
    print(f"    Trades={s3['trades']}  Wins={s3['wins']}  PnL=${s3['total_pnl']} ({s3['total_pnl_pct']}%)")

    for name, tlist in [("InverseMomentumETF", t1),
                         ("DefensiveRotation", t2),
                         ("CryptoMeanReversionExtreme", t3)]:
        if tlist:
            pd.DataFrame(tlist).to_csv(
                os.path.join(OUT_DIR, f"{name}_{year}.csv"), index=False
            )

    return results


def main():
    all_results = {}
    for year, (start, end) in PERIODS.items():
        all_results[year] = run_period(year, start, end)

    # Markdown report
    lines = [
        "# Backtest: 3 Estrategias Bajistas/Defensivas",
        "",
        "**Periodos:** COVID-crash 2020 | Bear market 2022",
        "**Portfolio inicial:** $200 | **Slippage:** 0.1% | **Data:** Daily (yfinance)",
        f"**Warmup:** {WARMUP_DAYS} dias previos para inicializar SMA200/MACD/RSI",
        "",
        "> Nota: Backtest en escala DAILY. En produccion las estrategias usan barras de 5m.",
        "> Los resultados en daily son mas conservadores (menos senales) pero validan la logica.",
        "",
    ]

    for year in ["2020", "2022"]:
        lines += [f"## {year}", "",
                  "| Estrategia | Trades | Win% | PnL$ | PnL% | MaxDD% | Sharpe |",
                  "|---|---|---|---|---|---|---|"]
        for name, s in all_results[year].items():
            wr  = f"{s['win_rate']*100:.0f}%" if s["win_rate"] is not None else "-"
            sh  = str(s["sharpe"]) if s["sharpe"] is not None else "-"
            ico = "+" if s["total_pnl"] >= 0 else ""
            lines.append(f"| {name} | {s['trades']} | {wr} | "
                         f"{ico}${s['total_pnl']} | {ico}{s['total_pnl_pct']}% | "
                         f"{s['max_dd']}% | {sh} |")
        lines.append("")

    lines += ["## Veredicto", ""]
    for name in ["InverseMomentumETF", "DefensiveRotation", "CryptoMeanReversionExtreme"]:
        p20 = all_results["2020"][name]["total_pnl"]
        p22 = all_results["2022"][name]["total_pnl"]
        lines.append(
            f"- **{name}** | 2020: {'RENTABLE +$' if p20>0 else 'PERDIDA -$'}{abs(p20)} "
            f"| 2022: {'RENTABLE +$' if p22>0 else 'PERDIDA -$'}{abs(p22)}"
        )

    lines += ["", "## Advertencias Importantes", "",
              "1. **ETFs 3x inversos (SQQQ/SPXU):** tienen decay por rebalanceo diario que "
              "no se captura en un backtest diario — en produccion real el decay REDUCE las ganancias.",
              "2. **DefensiveRotation:** KO/PG/JNJ/WMT/PEP son defensivos pero no inmunes — "
              "en crashes sistemicos bajan con el mercado aunque menos.",
              "3. **CryptoMeanReversionExtreme:** los rebounds extremos (RSI<25) son raros en daily. "
              "En 5m (produccion) ocurren mucho mas frecuentemente.",
              "",
              f"*Generado: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}*", ""]

    rpath = os.path.join(os.path.dirname(__file__), "RESULTS.md")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n\n{'='*60}")
    print("  RESUMEN FINAL")
    print(f"{'='*60}")
    for year in ["2020", "2022"]:
        print(f"\n  [{year}]")
        for name, s in all_results[year].items():
            sign = "+" if s["total_pnl"] >= 0 else ""
            print(f"    {name:<35} {sign}${s['total_pnl']}  ({sign}{s['total_pnl_pct']}%)")

    print(f"\n  Reporte: {rpath}")
    print(f"  CSVs:    {OUT_DIR}/")


if __name__ == "__main__":
    main()
