import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=5)

_, stdout, stderr = ssh.exec_command('/opt/trader/venv/bin/python3 /opt/trader/test_crypto.py')
print("OUT:", stdout.read().decode('utf-8'))
print("ERR:", stderr.read().decode('utf-8'))
ssh.close()
