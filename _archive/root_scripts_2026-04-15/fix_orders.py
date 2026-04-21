
import sys
sys.path.insert(0, "/opt/trader")
from dotenv import load_dotenv
load_dotenv("/opt/trader/.env")
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from datetime import datetime, timedelta

api_key = os.environ.get("ALPACA_API_KEY","")
secret_key = os.environ.get("ALPACA_SECRET_KEY","")
client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)

# Cancel all NEW SOL orders
orders = client.get_orders(GetOrdersRequest(status="open"))
print("Open orders to cancel:")
for o in orders:
    print(f"  {o.id[:8]} {o.symbol} {o.side} {o.status} qty={o.qty}")
    client.cancel_order_by_id(o.id)
    print(f"  Canceled {o.id[:8]}")

# Check account
acc = client.get_account()
print(f"Equity: {acc.equity}, Cash: {acc.cash}")
