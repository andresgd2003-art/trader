import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

ssh.exec_command('killall -9 python3')
time.sleep(1)
ssh.exec_command('cd /opt/trader && git reset --hard && git pull')
time.sleep(2)
ssh.exec_command('systemctl start alpacatrader.service')
time.sleep(8)

_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -n 50 --no-pager | egrep -i "Descargando historial|Historial inyectado|engine|fallback"')
log = stdout.read().decode('utf-8', errors='ignore')
print(log.encode('ascii', 'ignore').decode('ascii'))

ssh.close()
