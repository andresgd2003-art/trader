
import os, sys
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest

api_key = os.environ.get("ALPACA_API_KEY","")
secret_key = os.environ.get("ALPACA_SECRET_KEY","")
client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)

orders = client.get_orders(GetOrdersRequest(status="all", limit=20))
for o in orders:
    print(o.created_at, o.symbol, o.side, o.status, o.notional, o.qty)

positions = client.get_all_positions()
print("Positions:", [(p.symbol, p.qty, p.current_price) for p in positions])
