import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

_, stdout, _ = ssh.exec_command('cat /etc/systemd/system/alpacatrader.service')
print("systemd conf:")
print(stdout.read().decode('utf-8'))

_, stdout, _ = ssh.exec_command('cat /opt/trader/.env')
print("\n.env conf:")
print(stdout.read().decode('utf-8'))

_, stdout, _ = ssh.exec_command('systemctl status alpacatrader.service | cat')
print("\nstatus:")
print(stdout.read().decode('utf-8'))

ssh.close()
