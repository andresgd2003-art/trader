
import sys
sys.path.insert(0, "/opt/trader")
from dotenv import load_dotenv
load_dotenv("/opt/trader/.env")
import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest

api_key = os.environ.get("ALPACA_API_KEY","")
secret_key = os.environ.get("ALPACA_SECRET_KEY","")
client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)

orders = client.get_orders(GetOrdersRequest(status="open"))
print(f"Open orders: {len(orders)}")
for o in orders:
    oid = str(o.id)
    print(f"  {oid[:8]} {o.symbol} {o.side} {o.status} qty={o.qty}")
    client.cancel_order_by_id(o.id)
    print(f"  Canceled OK")

acc = client.get_account()
print(f"Equity: {acc.equity}  Cash: {acc.cash}")
print(f"Buying Power: {acc.buying_power}")
