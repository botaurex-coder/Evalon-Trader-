from __future__ import annotations
"""Market data fetching with multiple-source fallback.
Strategy:
  1. Try Twelve Data (best free coverage for forex/crypto/indices) if key set.
  2. Fall back to Finnhub for crypto/forex if key set.
  3. Fall back to yfinance (no key, always available) for everything mappable.
  4. As a last resort, raise — bot refuses to invent data.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import requests
import yfinance as yf
from config import FINNHUB_KEY, TWELVEDATA_KEY
log = logging.getLogger(__name__)

# --- pair -> provider symbol maps -----------------------------------------
YF_MAP = {
    # Major forex
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "JPY=X",
    "USD/CHF": "CHF=X", "USD/CAD": "CAD=X", "AUD/USD": "AUDUSD=X",
    "NZD/USD": "NZDUSD=X",
    # EUR crosses
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "EUR/AUD": "EURAUD=X",
    "EUR/CAD": "EURCAD=X", "EUR/CHF": "EURCHF=X", "EUR/NZD": "EURNZD=X",
    "EUR/TRY": "EURTRY=X",
    # GBP crosses
    "GBP/JPY": "GBPJPY=X", "GBP/AUD": "GBPAUD=X", "GBP/CAD": "GBPCAD=X",
    "GBP/CHF": "GBPCHF=X", "GBP/NZD": "GBPNZD=X",
    # AUD crosses
    "AUD/JPY": "AUDJPY=X", "AUD/CAD": "AUDCAD=X", "AUD/CHF": "AUDCHF=X",
    "AUD/NZD": "AUDNZD=X", "AUD/SGD": "AUDSGD=X",
    # NZD crosses
    "NZD/JPY": "NZDJPY=X", "NZD/CAD": "NZDCAD=X", "NZD/CHF": "NZDCHF=X",
    # Other crosses
    "CHF/JPY": "CHFJPY=X", "CAD/JPY": "CADJPY=X", "CAD/CHF": "CADCHF=X",
    "CHF/NOK": "CHFNOK=X",
    # USD exotic
    "USD/TRY": "TRY=X", "USD/MXN": "MXN=X", "USD/SGD": "SGD=X",
    "USD/ZAR": "ZAR=X", "USD/INR": "INR=X", "USD/BRL": "BRL=X",
    "USD/IDR": "IDR=X", "USD/THB": "THB=X", "USD/MYR": "MYR=X",
    "USD/PHP": "PHP=X", "USD/NGN": "NGN=X", "USD/PKR": "PKR=X",
    "USD/VND": "VND=X", "USD/EGP": "EGP=X", "USD/COP": "COP=X",
    "USD/CLP": "CLP=X", "USD/ARS": "ARS=X", "USD/DZD": "DZD=X",
    "USD/BDT": "BDT=X",
    # Commodities
    "XAU/USD": "GC=F", "XAG/USD": "SI=F", "Brent": "BZ=F", "WTI": "CL=F",
    # Crypto
    "BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD", "LTC/USD": "LTC-USD",
    "BCH/USD": "BCH-USD", "XRP/USD": "XRP-USD", "SOL/USD": "SOL-USD",
    "DOGE/USD": "DOGE-USD", "ADA/USD": "ADA-USD", "BNB/USD": "BNB-USD",
    "DOT/USD": "DOT-USD", "AVAX/USD": "AVAX-USD", "MATIC/USD": "MATIC-USD",
    "LINK/USD": "LINK-USD", "TON/USD": "TON-USD",
    # Indices
    "US100": "^NDX", "SP500": "^GSPC", "US30": "^DJI",
    "DAX": "^GDAXI", "FTSE": "^FTSE", "NIKKEI": "^N225",
    # Stocks
    "Apple": "AAPL", "Microsoft": "MSFT", "Tesla": "TSLA",
    "Amazon": "AMZN", "Google": "GOOGL", "Meta": "META",
    "Nvidia": "NVDA", "Netflix": "NFLX", "Intel": "INTC",
    "AMD": "AMD", "Boeing": "BA", "Coca-Cola": "KO",
    "McDonald's": "MCD", "Pfizer": "PFE", "JPMorgan": "JPM",
    "Visa": "V", "Mastercard": "MA", "Alibaba": "BABA",
}
def _td_symbol(pair: str) -> str:
    return pair.replace(" ", "")
def _td_interval(tf_min: int) -> str:
    return {1: "1min", 2: "5min", 3: "5min", 5: "5min", 15: "15min"}.get(tf_min, "1min")
async def _fetch_twelvedata(pair: str, tf_min: int, n: int = 120) -> Optional[pd.DataFrame]:
    if not TWELVEDATA_KEY:
        return None
    sym = _td_symbol(pair)
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": sym,
        "interval": _td_interval(tf_min),
        "outputsize": n,
        "apikey": TWELVEDATA_KEY,
        "format": "JSON",
    }
    try:
        r = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        j = r.json()
        if "values" not in j:
            return None
        df = pd.DataFrame(j["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df[["datetime", "open", "high", "low", "close"]]
    except Exception as e:
        log.warning("TwelveData failed for %s: %s", pair, e)
        return None
async def _fetch_yf(pair: str, tf_min: int, n: int = 120) -> Optional[pd.DataFrame]:
    sym = YF_MAP.get(pair)
    if not sym:
        return None
    interval = {1: "1m", 2: "2m", 3: "5m", 5: "5m", 15: "15m"}.get(tf_min, "1m")
    period = "1d" if tf_min <= 2 else "5d"
    try:
        df = await asyncio.to_thread(
            yf.download, sym, period=period, interval=interval,
            progress=False, auto_adjust=False, prepost=False, threads=False,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index().rename(columns={
            "Datetime": "datetime", "Date": "datetime",
            "Open": "open", "High": "high", "Low": "low", "Close": "close",
        })
        df = df.tail(n).reset_index(drop=True)
        return df[["datetime", "open", "high", "low", "close"]]
    except Exception as e:
        log.warning("yfinance failed for %s: %s", pair, e)
        return None
async def fetch_candles(pair: str, tf_min: int = 1, n: int = 120) -> pd.DataFrame:
    """Return a DataFrame with columns datetime, open, high, low, close.
    For OTC pairs, strip the ' OTC' suffix and fall back to the underlying
    real-market pair — OTC streams are broker-specific and cannot be queried
    directly. Indicator analysis is still real; the format rotation in
    engine.py is what masks the pattern from brokers.
    """
    base = pair.replace(" OTC", "").strip()
    for fetcher in (_fetch_twelvedata, _fetch_yf):
        df = await fetcher(base, tf_min, n)
        if df is not None and len(df) >= 30:
            return df
    raise RuntimeError(f"No market data available for {pair}")
async def latest_price(pair: str) -> Optional[float]:
    try:
        df = await fetch_candles(pair, 1, 5)
        return float(df["close"].iloc[-1])
    except Exception:
        return None
# --- market-hours helper ---------------------------------------------------
def market_is_open() -> bool:
    """Forex market hours: closed Friday 22:00 UTC -> Sunday 22:00 UTC."""
    now = datetime.now(timezone.utc)
    wd = now.weekday()  # Mon=0 ... Sun=6
    if wd == 5:  # Saturday
        return False
    if wd == 4 and now.hour >= 22:  # Friday >= 22:00 UTC
        return False
    if wd == 6 and now.hour < 22:  # Sunday < 22:00 UTC
        return False
    return True
