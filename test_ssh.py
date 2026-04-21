import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', pkey=paramiko.Ed25519Key.from_private_key_file(r'C:\Users\user\.ssh\trader_vps_new'), timeout=15)
cmd = 'curl -s http://localhost:8000/api/strategy/ranking?sort_by=profit_factor | jq ''.[0]'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(stdout.read().decode('utf-8'))
ssh.close()
