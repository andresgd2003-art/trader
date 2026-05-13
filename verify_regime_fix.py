import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
key_path = r'C:\Users\user\.ssh\trader_vps_new'
pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15)

# Trigger assessment via API
print("Triggering assessment via API...")
ssh.exec_command('curl -s http://localhost:8000/api/market-regime')

import time
time.sleep(2) # Wait for log to write

# Check logs for the NEW format
print("Checking engine.log for NEW format...")
stdin, stdout, stderr = ssh.exec_command('grep "Regime" /opt/trader/data/engine.log | tail -n 5')
print(stdout.read().decode('utf-8'))

ssh.close()
