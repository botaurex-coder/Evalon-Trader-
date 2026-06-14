"""Signal generation engine — multi-indicator consensus + OTC format rotation."""
from __future__ import annotations
import random
import time
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import db
import indicators as ind
from market import fetch_candles
@dataclass
class Signal:
    direction: str   # "BUY" | "SELL"
    strength: int    # 0..100
    entry: Optional[float]
    raw_dir: str     # what indicators actually said, before OTC transform
# --- consensus computation -------------------------------------------------
def _consensus(df: pd.DataFrame) -> tuple[str, int]:
    """Return ('BUY'|'SELL'|'NONE', strength 0..100) based on >20 votes."""
    close = df["close"]
    votes_up = 0
    votes_dn = 0
    total = 0
    def vote(up: bool, weight: int = 1) -> None:
        nonlocal votes_up, votes_dn, total
        total += weight
        if up:
            votes_up += weight
        else:
            votes_dn += weight
    # 1) EMA stack
    e9, e21, e50 = ind.ema(close, 9).iloc[-1], ind.ema(close, 21).iloc[-1], ind.ema(close, 50).iloc[-1]
    vote(e9 > e21, 2)
    vote(e21 > e50, 2)
    vote(close.iloc[-1] > e9, 1)
    vote(close.iloc[-1] > e50, 1)
    # 2) RSI
    r = ind.rsi(close, 14).iloc[-1]
    if r < 30: vote(True, 2)
    elif r > 70: vote(False, 2)
    else: vote(r > 50, 1)
    r7 = ind.rsi(close, 7).iloc[-1]
    vote(r7 > 50, 1)
    # 3) MACD
    line, sig, hist = ind.macd(close)
    vote(line.iloc[-1] > sig.iloc[-1], 2)
    vote(hist.iloc[-1] > 0, 1)
    vote(hist.iloc[-1] > hist.iloc[-2], 1)
    # 4) Bollinger
    upper, mid, lower = ind.bollinger(close)
    c = close.iloc[-1]
    if c < lower.iloc[-1]: vote(True, 2)
    elif c > upper.iloc[-1]: vote(False, 2)
    else: vote(c > mid.iloc[-1], 1)
    # 5) Stochastic
    k, d = ind.stochastic(df)
    vote(k.iloc[-1] > d.iloc[-1], 1)
    if k.iloc[-1] < 20: vote(True, 1)
    elif k.iloc[-1] > 80: vote(False, 1)
    # 6) ADX-weighted EMA trend
    a = ind.adx(df).iloc[-1]
    if pd.notna(a) and a > 25:
        vote(e9 > e21, 2)
    # 7) CCI
    cc = ind.cci(df).iloc[-1]
    if cc < -100: vote(True, 1)
    elif cc > 100: vote(False, 1)
    else: vote(cc > 0, 1)
    # 8) Williams %R
    w = ind.williams_r(df).iloc[-1]
    if w < -80: vote(True, 1)
    elif w > -20: vote(False, 1)
    # 9) MFI
    m = ind.mfi(df).iloc[-1]
    if pd.notna(m):
        if m < 20: vote(True, 1)
        elif m > 80: vote(False, 1)
        else: vote(m > 50, 1)
    # 10) Price action: last 3 candles momentum
    vote(close.iloc[-1] > close.iloc[-2], 1)
    vote(close.iloc[-2] > close.iloc[-3], 1)
    # 11) Higher-high / lower-low structure
    hh = df["high"].iloc[-5:].max() == df["high"].iloc[-1]
    ll = df["low"].iloc[-5:].min() == df["low"].iloc[-1]
    if hh: vote(True, 1)
    if ll: vote(False, 1)
    if total == 0:
        return "NONE", 0
    pct_up = votes_up / total
    if pct_up >= 0.55:
        return "BUY", int(round(pct_up * 100))
    if pct_up <= 0.45:
        return "SELL", int(round((1 - pct_up) * 100))
    return "NONE", int(round(max(pct_up, 1 - pct_up) * 100))
# --- OTC format rotation ---------------------------------------------------
ROTATE_AFTER_SECONDS = 30 * 60   # change format roughly every 30 min
ROTATE_AFTER_STREAK = 5          # max same-direction streak in format 2
def _otc_transform(raw_dir: str) -> str:
    state = db.get_otc_state()
    now = int(time.time())
    fmt = state["format"]
    streak_dir = state["streak_dir"]
    streak_n = state["streak_n"]
    changed_at = state["changed_at"]
    # Rotate format on a timer (random pick of 1/2/3, weighted)
    if now - changed_at > ROTATE_AFTER_SECONDS:
        fmt = random.choices([1, 2, 3], weights=[3, 2, 5], k=1)[0]
        streak_dir, streak_n = None, 0
    if fmt == 1:  # reverse
        out = "SELL" if raw_dir == "BUY" else "BUY"
    elif fmt == 2:  # streak
        if streak_dir is None or streak_n >= ROTATE_AFTER_STREAK:
            streak_dir = random.choice(["BUY", "SELL"])
            streak_n = 0
        out = streak_dir
        streak_n += 1
    else:  # normal
        out = raw_dir
    db.set_otc_state(fmt, streak_dir, streak_n)
    return out
# --- public API ------------------------------------------------------------
async def analyze(pair: str, tf_min: int) -> Optional[Signal]:
    is_otc = pair.endswith(" OTC")
    try:
        df = await fetch_candles(pair, tf_min=tf_min, n=120)
        raw, strength = _consensus(df)
        entry = float(df["close"].iloc[-1])
    except Exception:
        if is_otc:
            # OTC: no real data available, use random fallback
            raw = random.choice(["BUY", "SELL"])
            strength = 60
            entry = None
        else:
            # Non-OTC: refuse to invent data
            return None
    else:
        if is_otc and raw == "NONE":
            raw = random.choice(["BUY", "SELL"])
            strength = 60
        elif not is_otc and raw == "NONE":
            return None
    if is_otc:
        final_dir = _otc_transform(raw)
    else:
        final_dir = raw
    return Signal(direction=final_dir, strength=strength, entry=entry, raw_dir=raw)
async def best_timeframe(pair: str) -> Optional[tuple[int, Signal]]:
    """Bot Picks: scan 1m, 2m, 3m and return the strongest passing signal."""
    best: Optional[tuple[int, Signal]] = None
    for tf in (1, 2, 3):
        try:
            sig = await analyze(pair, tf)
        except Exception:
            continue
        if not sig:
            continue
        if sig.strength < 70:
            continue
        if best is None or sig.strength > best[1].strength:
            best = (tf, sig)
    return best
