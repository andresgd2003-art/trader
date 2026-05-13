import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
key_path = r'C:\Users\user\.ssh\trader_vps_new'
pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15)

print("Checking for recent startup...")
stdin, stdout, stderr = ssh.exec_command('grep "Trading Engine arrancando" /opt/trader/data/engine.log | tail -n 2')
print(stdout.read().decode('utf-8'))

print("Checking for NEW format regime log...")
# Looking for "Sizing:" which is in the new format, vs "Sizing activo:" in the old one.
stdin, stdout, stderr = ssh.exec_command('grep "Regime" /opt/trader/data/engine.log | grep "Sizing:" | tail -n 5')
print(stdout.read().decode('utf-8'))

print("Checking for any errors in regime_manager...")
stdin, stdout, stderr = ssh.exec_command('grep -i "regime_manager" /opt/trader/data/engine.log | grep -i "error\\|warning" | tail -n 5')
print(stdout.read().decode('utf-8'))

ssh.close()
