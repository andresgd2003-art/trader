import paramiko
import os

key_path = r'C:\Users\user\.ssh\trader_vps_new'
host = '148.230.82.14'
user = 'root'

files_to_deploy = [
    (r'c:\Users\user\OneDrive\Escritorio\gemini cli\trader\strategies\strat_09_pairs.py', '/opt/trader/strategies/strat_09_pairs.py'),
    (r'c:\Users\user\OneDrive\Escritorio\gemini cli\trader\strategies\strat_07_vix_filter.py', '/opt/trader/strategies/strat_07_vix_filter.py'),
    (r'c:\Users\user\OneDrive\Escritorio\gemini cli\trader\strategies_crypto\strat_02_bb_breakout.py', '/opt/trader/strategies_crypto/strat_02_bb_breakout.py'),
]

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
    ssh.connect(host, port=22, username=user, pkey=pkey, timeout=15)

    sftp = ssh.open_sftp()
    for local, remote in files_to_deploy:
        print(f"Deploying {local} -> {remote}...")
        sftp.put(local, remote)
    sftp.close()
    
    print("Upload successful. Restarting service...")
    ssh.exec_command('systemctl restart alpacatrader')
    print("Service restarted.")
    ssh.close()

except Exception as e:
    print(f"Deployment failed: {e}")
