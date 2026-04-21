import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

time.sleep(15)
_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -b --since "17:52:40" --no-pager | egrep -i "TENDENCIA ACTIVA|COMPRANDO|VENDIENDO|BREAKOUT|GOLDEN CROSS|MACD cruz|Historial inyectado|Regime" | tail -30')
log = stdout.read().decode('utf-8', errors='ignore')
print(log.encode('ascii', 'ignore').decode('ascii'))
ssh.close()
