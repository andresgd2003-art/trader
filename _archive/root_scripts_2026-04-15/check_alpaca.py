import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

check = '''
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
'''
with open("check_orders.py","w") as f: f.write(check)

import paramiko
sftp = ssh.open_sftp()
sftp.put("check_orders.py", "/opt/trader/check_orders.py")
sftp.close()

_, stdout, stderr = ssh.exec_command('cd /opt/trader && /opt/trader/venv/bin/python3 check_orders.py')
print(stdout.read().decode('utf-8', errors='ignore'))
print("ERR:", stderr.read().decode('utf-8', errors='ignore')[:300])
ssh.close()
