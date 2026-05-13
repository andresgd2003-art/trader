import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
key_path = r'C:\Users\user\.ssh\trader_vps_new'
pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15)

# 1. Get regime assessment logs
print("=== REGIME ASSESSMENTS ===")
stdin, stdout, stderr = ssh.exec_command('grep "Regime" /opt/trader/data/engine.log | grep -i "SPY\\|VIX\\|sizing\\|BULL\\|BEAR\\|CHOP\\|UNKNOWN" | tail -n 10')
print(stdout.read().decode('utf-8'))

# 2. Get the current regime state from the API
print("=== CURRENT REGIME FROM API ===")
stdin, stdout, stderr = ssh.exec_command('curl -s http://localhost:8000/api/regime 2>/dev/null || echo "API not accessible"')
print(stdout.read().decode('utf-8'))

# 3. Get all Soft Guard blocks (strategy blocked by regime)
print("=== SOFT GUARD BLOCKS (last 20) ===")
stdin, stdout, stderr = ssh.exec_command('grep -i "soft.guard\\|is_strategy_enabled\\|Soft Guard" /opt/trader/data/engine.log | tail -n 20')
print(stdout.read().decode('utf-8'))

# 4. Check how often regime re-evaluates
print("=== REGIME RE-EVALUATIONS ===")
stdin, stdout, stderr = ssh.exec_command('grep "Regime" /opt/trader/data/engine.log | grep -c "SPY="')
print("Total regime assessments:", stdout.read().decode('utf-8'))

# 5. Check regime transitions (changes from one to another)
print("=== REGIME TRANSITIONS ===")
stdin, stdout, stderr = ssh.exec_command('grep "Regime" /opt/trader/data/engine.log | grep "SPY=" | head -n 20')
print(stdout.read().decode('utf-8'))

ssh.close()
