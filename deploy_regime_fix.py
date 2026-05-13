import paramiko
import os

key_path = r'C:\Users\user\.ssh\trader_vps_new'
local_file = r'c:\Users\user\OneDrive\Escritorio\gemini cli\trader\engine\regime_manager.py'
remote_file = '/opt/trader/engine/regime_manager.py'
host = '148.230.82.14'
user = 'root'

print(f"Deploying {local_file} to {host}:{remote_file}...")

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
    ssh.connect(host, port=22, username=user, pkey=pkey, timeout=15)

    sftp = ssh.open_sftp()
    sftp.put(local_file, remote_file)
    sftp.close()
    
    print("Upload successful.")
    
    # Optional: Restart service if needed. Assuming it picks up changes on next assess call
    # or requires a full restart. Let's just check the log to see it assess with new logic.
    print("Verifying logs...")
    stdin, stdout, stderr = ssh.exec_command('tail -n 10 /opt/trader/data/engine.log')
    print(stdout.read().decode('utf-8'))
    
    ssh.close()
    print("Deployment finished.")

except Exception as e:
    print(f"Deployment failed: {e}")
