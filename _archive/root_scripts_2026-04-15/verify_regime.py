import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

# Verificar que el archivo tiene el valor correcto
_, stdout, _ = ssh.exec_command('grep "VIX_BULL_THRESHOLD" /opt/trader/engine/regime_manager.py')
print("Current value on VPS:", stdout.read().decode().strip())

ssh.exec_command('systemctl restart alpacatrader.service')
time.sleep(35)

_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -b -n 5 --no-pager | egrep -i "Regimen|Regime|BULL|CHOP"')
log = stdout.read().decode('utf-8', errors='ignore')
print(log.encode('ascii', 'ignore').decode('ascii'))

ssh.close()
