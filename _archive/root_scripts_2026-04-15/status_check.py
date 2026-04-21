import paramiko, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('148.230.82.14', port=22, username='root', password='VPS_PASSWORD_REDACTED', timeout=10)

# Estado del servicio
_, stdout, _ = ssh.exec_command('systemctl status alpacatrader.service | head -5')
print("=== ESTADO ===")
print(stdout.read().decode('utf-8', errors='ignore').encode('ascii','ignore').decode())

# Verificar que el codigo nuevo esta en VPS
_, stdout, _ = ssh.exec_command('grep "RSI_BUY\|RSI_SELL\|HIGH_PERIOD\|TENDENCIA ACTIVA" /opt/trader/strategies/strat_01_macross.py /opt/trader/strategies/strat_02_donchian.py /opt/trader/strategies/strat_04_macd.py /opt/trader/strategies/strat_05_rsi_dip.py')
print("=== PARAMS EN VPS ===")
print(stdout.read().decode('utf-8', errors='ignore').encode('ascii','ignore').decode())

# Ultimas acciones relevantes
_, stdout, _ = ssh.exec_command('journalctl -u alpacatrader.service -b -n 30 --no-pager | egrep -iv "Charts|Scoring|DailyMode|RSI=|MACD=|SMA" | tail -20')
print("=== ULTIMAS ACCIONES ===")
print(stdout.read().decode('utf-8', errors='ignore').encode('ascii','ignore').decode())

ssh.close()
