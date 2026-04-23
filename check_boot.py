import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
pkey = paramiko.Ed25519Key.from_private_key_file(r'C:\Users\user\.ssh\trader_vps_new')
ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15)

cmds = [
    'journalctl -u alpacatrader --since "2 min ago" --no-pager | grep -i "estrategia\\|registered\\|inicializada\\|error\\|ERROR\\|critical" | tail -30',
    'journalctl -u alpacatrader --since "2 min ago" --no-pager | grep -i "bollinger\\|grid\\|rotation\\|donchian\\|vix\\|ribbon\\|micro-vwap" | tail -20',
]
for cmd in cmds:
    print(f"\n>>> {cmd[:80]}...")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', 'replace').strip()
    if out:
        print(out)
    else:
        print("(no output)")

ssh.close()
