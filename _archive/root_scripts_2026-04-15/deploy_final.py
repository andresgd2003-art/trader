import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

ssh.exec_command('cd /opt/trader && git pull --ff-only')
time.sleep(2)
ssh.exec_command('systemctl restart alpacatrader.service')
time.sleep(50)

_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -b --since "17:52:20" --no-pager | egrep -iv "Charts|Scoring|DailyMode|RSI=|SMA|MACD=|Canal:" | tail -40')
log = stdout.read().decode('utf-8', errors='ignore')
print(log.encode('ascii', 'ignore').decode('ascii'))
ssh.close()
