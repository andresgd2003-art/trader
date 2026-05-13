import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get('ALPACA_API_KEY')
secret_key = os.environ.get('ALPACA_SECRET_KEY')
base_url = 'https://paper-api.alpaca.markets' if os.environ.get('PAPER_TRADING', 'True').lower() == 'true' else 'https://api.alpaca.markets'

headers = {
    'APCA-API-KEY-ID': api_key,
    'APCA-API-SECRET-KEY': secret_key
}

res = requests.get(f'{base_url}/v2/account', headers=headers)
account = res.json()
print(f"Day Trade Count: {account.get('daytrade_count')}")
print(f"Account Equity: {account.get('equity')}")

res2 = requests.get(f'{base_url}/v2/account/activities/FILL', headers=headers, params={'direction': 'desc'})
activities = res2.json()
print(f"Found {len(activities)} recent fill activities.")
for act in activities[:40]:
    print(f"{act.get('transaction_time')} - {act.get('side')} {act.get('qty')} {act.get('symbol')} @ {act.get('price')}")
