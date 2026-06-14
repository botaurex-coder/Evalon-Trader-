"""Static configuration for Evalon Winners trading-signals bot."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram --------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = 8535925646
CHANNEL_ID = -1003403743370
CHANNEL_INVITE = "https://t.me/+mRNfGaNhz3RkZGRk"
SUPPORT_BOT = "Evalonwinnersbot"

# --- Market data providers -------------------------------------------------
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "").strip()
TWELVEDATA_KEY = os.getenv("TWELVEDATA_KEY", "").strip()

# --- Defaults --------------------------------------------------------------
IMG_BUY = os.getenv("IMG_BUY", "https://i.imgur.com/8wZkQ2T.png")
IMG_SELL = os.getenv("IMG_SELL", "https://i.imgur.com/2yQ9pPV.png")

FREE_SIGNAL_LIMIT = 3
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = os.getenv("DB_PATH", "bot.db")
PORT = int(os.getenv("PORT", "10000"))

# --- Pairs -----------------------------------------------------------------
NON_OTC_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD",
    "EUR/GBP", "EUR/JPY", "EUR/AUD", "EUR/CAD", "EUR/CHF",
    "GBP/JPY", "GBP/AUD", "GBP/CAD", "GBP/CHF",
    "AUD/JPY", "AUD/CAD", "AUD/CHF",
    "CHF/JPY", "CAD/JPY", "CAD/CHF",
    "XAU/USD", "XAG/USD",
    "BTC/USD", "ETH/USD",
    "US100", "SP500",
]

OTC_PAIRS = [
    "EUR/USD OTC", "GBP/USD OTC", "USD/JPY OTC", "USD/CHF OTC", "USD/CAD OTC",
    "AUD/USD OTC", "NZD/USD OTC",
    "EUR/GBP OTC", "EUR/JPY OTC", "EUR/AUD OTC", "EUR/CAD OTC", "EUR/CHF OTC",
    "EUR/NZD OTC", "GBP/JPY OTC", "GBP/AUD OTC", "GBP/CAD OTC", "GBP/CHF OTC",
    "GBP/NZD OTC", "AUD/JPY OTC", "AUD/CAD OTC", "AUD/CHF OTC", "AUD/NZD OTC",
    "NZD/JPY OTC", "NZD/CAD OTC", "NZD/CHF OTC",
    "CHF/JPY OTC", "CAD/JPY OTC", "CAD/CHF OTC",
    "USD/ARS OTC", "USD/BDT OTC", "USD/BRL OTC", "USD/CLP OTC", "USD/COP OTC",
    "USD/DZD OTC", "USD/EGP OTC", "USD/IDR OTC", "USD/INR OTC", "USD/MXN OTC",
    "USD/MYR OTC", "USD/NGN OTC", "USD/PHP OTC", "USD/PKR OTC", "USD/SGD OTC",
    "USD/THB OTC", "USD/TRY OTC", "USD/VND OTC", "USD/ZAR OTC",
    "EUR/TRY OTC", "AUD/SGD OTC", "CHF/NOK OTC",
    "XAU/USD OTC", "XAG/USD OTC", "Brent OTC", "WTI OTC",
    "BTC/USD OTC", "ETH/USD OTC", "LTC/USD OTC", "BCH/USD OTC", "XRP/USD OTC",
    "SOL/USD OTC", "DOGE/USD OTC", "ADA/USD OTC", "BNB/USD OTC", "DOT/USD OTC",
    "AVAX/USD OTC", "MATIC/USD OTC", "LINK/USD OTC", "TON/USD OTC",
    "US100 OTC", "SP500 OTC", "US30 OTC", "DAX OTC", "FTSE OTC", "NIKKEI OTC",
    "Apple OTC", "Microsoft OTC", "Tesla OTC", "Amazon OTC", "Google OTC",
    "Meta OTC", "Nvidia OTC", "Netflix OTC", "Intel OTC", "AMD OTC",
    "Boeing OTC", "Coca-Cola OTC", "McDonald's OTC", "Pfizer OTC",
    "JPMorgan OTC", "Visa OTC", "Mastercard OTC", "Alibaba OTC",
]
