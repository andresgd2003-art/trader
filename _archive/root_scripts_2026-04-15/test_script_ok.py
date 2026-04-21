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
    print("DataFrame Length:", len(bars.df))
    print("Keys in bars data:", bars.data.keys() if hasattr(bars, "data") else "No data attr")
except Exception as e:
    print("Error:", e)
'''
with open("test_crypto_s.py", "w", encoding="utf-8") as f: f.write(remote_script)

sftp = ssh.open_sftp()
sftp.put("test_crypto_s.py", "/opt/trader/test_crypto_s.py")
sftp.close()

_, stdout, stderr = ssh.exec_command('/opt/trader/venv/bin/python3 /opt/trader/test_crypto_s.py')
print("OUT:", stdout.read().decode('utf-8'))
print("ERR:", stderr.read().decode('utf-8'))
ssh.close()
