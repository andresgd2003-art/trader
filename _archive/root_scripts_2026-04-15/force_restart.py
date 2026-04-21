import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

ssh.exec_command('killall -9 python3')
time.sleep(3)
ssh.exec_command('systemctl start alpacatrader.service')
time.sleep(40)

_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -b --no-pager | egrep -i "Regimen|BULL|CHOP|BEAR" | tail -6')
log = stdout.read().decode('utf-8', errors='ignore')
print(log.encode('ascii', 'ignore').decode('ascii'))
ssh.close()
