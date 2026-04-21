import paramiko, time
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

# Matar el proceso principal a la fuerza
ssh.exec_command('killall -9 python3')
time.sleep(2)
# Iniciar limpio
ssh.exec_command('systemctl start alpacatrader.service')
time.sleep(3)

# Leer log reciente
_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -n 50 --no-pager | cat')
print(stdout.read().decode('utf-8', errors='replace'))

ssh.close()
