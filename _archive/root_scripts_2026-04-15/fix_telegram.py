import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

# 1. Update /opt/trader/.env
env_append = '''
TELEGRAM_BOT_TOKEN=TELEGRAM_TOKEN_REDACTED
TELEGRAM_CHAT_ID=7658439742
'''
ssh.exec_command('echo "{}" >> /opt/trader/.env'.format(env_append.strip()))

# 2. Update systemd service to include EnvironmentFile
sysd_cmd = '''
sed -i '/ExecStart=/i EnvironmentFile=/opt/trader/.env' /etc/systemd/system/alpacatrader.service
systemctl daemon-reload
systemctl restart alpacatrader.service
'''
ssh.exec_command(sysd_cmd)

time.sleep(3)
_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -b -n 20 --no-pager | grep "Telegram"')
print(stdout.read().decode('utf-8', errors='replace'))

ssh.close()
