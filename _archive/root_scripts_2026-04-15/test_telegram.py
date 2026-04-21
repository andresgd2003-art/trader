import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

_, stdout, _ = ssh.exec_command('cat /etc/systemd/system/alpacatrader.service')
print("--- systemd ---")
print(stdout.read().decode('utf-8', errors='replace'))

_, stdout, _ = ssh.exec_command('cat /opt/trader/.env')
print("\n--- .env ---")
print(stdout.read().decode('utf-8', errors='replace'))

ssh.close()
