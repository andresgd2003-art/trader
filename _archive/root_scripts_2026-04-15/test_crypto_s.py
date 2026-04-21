
import sys
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

try:
    client = CryptoHistoricalDataClient()
    req = CryptoBarsRequest(
        symbol_or_symbols=["BTC/USD", "SOL/USD"],
        timeframe=TimeFrame.Minute,
        start=datetime.utcnow() - timedelta(days=2)
    )
    bars = client.get_crypto_bars(req)
    print("DataFrame Length:", len(bars.df))
    print("Keys in bars data:", bars.data.keys() if hasattr(bars, "data") else "No data attr")
except Exception as e:
    print("Error:", e)
