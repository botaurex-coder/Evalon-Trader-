"""Pure-numpy/pandas technical indicators."""
from __future__ import annotations
import numpy as np
import pandas as pd
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()
def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))
def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig
def bollinger(close: pd.Series, period: int = 20, k: float = 2.0):
    m = sma(close, period)
    s = close.rolling(period).std()
    return m + k * s, m, m - k * s
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()
def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    low = df["low"].rolling(k_period).min()
    high = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low) / (high - low).replace(0, np.nan)
    return k, k.rolling(d_period).mean()
def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, period).replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(period).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(period).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(period).mean()
def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp - tp.rolling(period).mean()) / (0.015 * tp.rolling(period).std())
def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].rolling(period).max()
    low = df["low"].rolling(period).min()
    return -100 * (high - df["close"]) / (high - low).replace(0, np.nan)
def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index without volume (uses range as proxy when volume absent)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = (df["high"] - df["low"]).replace(0, 1e-9)
    mf = tp * vol
    pos = mf.where(tp > tp.shift(), 0)
    neg = mf.where(tp < tp.shift(), 0)
    mr = pos.rolling(period).sum() / neg.rolling(period).sum().replace(0, np.nan)
    return 100 - 100 / (1 + mr)
