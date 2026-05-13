import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

# Conexion por llave ed25519
key_path = r'C:\Users\user\.ssh\trader_vps_new'
pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15, allow_agent=False, look_for_keys=False)

cmds = [
    "cd /opt/trader && git fetch origin main && git reset --hard origin/main",
    "systemctl restart alpacatrader.service",
    "sleep 3",
    "systemctl status alpacatrader.service --no-pager -l | head -20",
]
for cmd in cmds:
    print(f"\n>>> {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', 'replace').strip()
    err = stderr.read().decode('utf-8', 'replace').strip()
    if out: print(out.encode('ascii', 'replace').decode())
    if err: print(f"STDERR: {err.encode('ascii', 'replace').decode()}")

ssh.close()
