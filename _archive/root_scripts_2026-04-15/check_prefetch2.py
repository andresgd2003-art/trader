import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service --since "2026-04-14 03:24:00" --until "2026-04-14 03:25:30" --no-pager | egrep -i "Descargando historial|Historial inyectado|engine"')
log = stdout.read().decode('utf-8', errors='ignore')
print(log.encode('ascii', 'ignore').decode('ascii'))

ssh.close()
