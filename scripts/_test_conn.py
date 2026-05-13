import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
pkey = paramiko.Ed25519Key.from_private_key_file(r'C:\Users\user\.ssh\trader_vps_new')
ssh.connect('148.230.82.14', port=22, username='root', pkey=pkey, timeout=15, allow_agent=False, look_for_keys=False)

cmds = [
    # Engine key events only - no strategy spam
    'journalctl -u alpacatrader.service --no-pager --since "3 min ago" 2>&1 | grep -iE "Post-history|Historial inyect|WebSocket|suscri|subscribe|COMPRA|VENTA|order|Engine.*strat|error|Regime|IEX|settled|notional|Arbiter|Grid" | tail -30',
    # Recent orders on Alpaca
    r"""cd /opt/trader && source venv/bin/activate && python3 << 'EOF'
import os; from dotenv import load_dotenv; load_dotenv('/opt/trader/.env')
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
key=os.environ.get('ALPACA_API_KEY',''); secret=os.environ.get('ALPACA_SECRET_KEY','')
client=TradingClient(api_key=key, secret_key=secret, paper=False)
orders=client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=5))
print(f"Last {len(orders)} orders:")
for o in orders:
    cid=str(o.client_order_id or '')[:35]
    print(f"  {o.submitted_at.strftime('%H:%M:%S')} {o.symbol} {o.side.value} {o.status.value} qty={o.qty} not={o.notional} cid={cid}")
acc=client.get_account()
print(f"Cash={float(acc.cash):.2f} Settled={float(getattr(acc,'settled_cash',0) or 0):.2f} BP={float(acc.buying_power):.2f}")
EOF""",
]
for cmd in cmds:
    print(f'\n>>> Running check...')
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', 'replace').strip()
    print(out.encode('ascii', 'replace').decode())
ssh.close()
