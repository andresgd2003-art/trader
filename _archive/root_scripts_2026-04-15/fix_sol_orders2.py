import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

check = '''
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
'''
with open("fix_orders2.py","w") as f: f.write(check)

sftp = ssh.open_sftp()
sftp.put("fix_orders2.py", "/opt/trader/fix_orders2.py")
sftp.close()

_, stdout, stderr = ssh.exec_command('cd /opt/trader && /opt/trader/venv/bin/python3 fix_orders2.py')
print(stdout.read().decode('utf-8', errors='ignore'))
err = stderr.read().decode('utf-8', errors='ignore')
if err: print("ERR:", err[:400])
ssh.close()
