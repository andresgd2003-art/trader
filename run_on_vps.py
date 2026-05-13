import paramiko
import os

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
key_path = r'C:\Users\user\.ssh\trader_vps_new'
pkey = paramiko.Ed25519Key.from_private_key_file(key_path)

ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15)

script = """
import os
import requests
from dotenv import load_dotenv

load_dotenv('/opt/trader/.env')
api_key = os.environ.get('ALPACA_API_KEY')
secret_key = os.environ.get('ALPACA_SECRET_KEY')
base_url = 'https://paper-api.alpaca.markets' if os.environ.get('PAPER_TRADING', 'True').lower() == 'true' else 'https://api.alpaca.markets'

headers = {'APCA-API-KEY-ID': api_key, 'APCA-API-SECRET-KEY': secret_key}

res = requests.get(f'{base_url}/v2/account', headers=headers)
account = res.json()
print('Day Trade Count:', account.get('daytrade_count'))

res2 = requests.get(f'{base_url}/v2/account/activities/FILL', headers=headers, params={'direction': 'desc'})
activities = res2.json()
print('Found', len(activities), 'recent fill activities.')

for act in activities[:100]:
    # We want to find BUY and SELL on the same day for the same symbol
    print(act.get('transaction_time')[:10], act.get('side'), act.get('qty'), act.get('symbol'))
"""

stdin, stdout, stderr = ssh.exec_command(f'/opt/trader/venv/bin/python -c "{script}"')
print("STDOUT:\n", stdout.read().decode('utf-8'))
print("STDERR:\n", stderr.read().decode('utf-8'))
ssh.close()
