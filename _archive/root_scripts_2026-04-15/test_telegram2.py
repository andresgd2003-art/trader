import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

time.sleep(10)
_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -n 50 --no-pager | egrep -i "telegram|notifier"')
print(stdout.read().decode('utf-8', errors='replace'))

ssh.close()
