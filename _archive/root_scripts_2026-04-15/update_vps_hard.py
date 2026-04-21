import paramiko, time
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

ssh.exec_command('cd /opt/trader && git reset --hard && git pull')
time.sleep(2)
_, stdout, stderr = ssh.exec_command('systemctl restart alpacatrader.service')

ssh.close()
