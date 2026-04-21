import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

remote_script = '''
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
    total = sum([len(v) for k,v in bars.items()]) if hasattr(bars, "items") else (len(bars.df) if not bars.df.empty else 0)
    print(f"Total bars fetched: {total}")
    if hasattr(bars, "items"):
        print([k for k in bars.keys()])
except Exception as e:
    print(f"Error: {e}")
'''
ssh.exec_command(f'echo "{remote_script}" > /opt/trader/test_crypto.py')
_, stdout, _ = ssh.exec_command('/opt/trader/venv/bin/python3 /opt/trader/test_crypto.py')
print(stdout.read().decode('utf-8'))
ssh.close()
