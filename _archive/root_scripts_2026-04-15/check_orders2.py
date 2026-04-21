
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

orders = client.get_orders(GetOrdersRequest(status="all", limit=15))
print("=== ORDERS ===")
for o in orders:
    print(f"{str(o.created_at)[:19]}  {o.symbol:6}  {o.side}  {o.status}  notional={o.notional}  qty={o.qty}")

print("=== POSITIONS ===")
positions = client.get_all_positions()
print([(p.symbol, p.qty, float(p.current_price), float(p.unrealized_pl)) for p in positions])
