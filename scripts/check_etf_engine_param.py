import os

folder = os.path.join(os.path.dirname(__file__), '..', 'strategies')
print('--- Verificacion ETF ---')
for f in sorted(os.listdir(folder)):
    if f.endswith('.py') and f != '__init__.py':
        path = os.path.join(folder, f)
        content = open(path, encoding='utf-8').read()
        count = content.count('is_strategy_enabled')
        # ETF es el default, no necesita pasar engine explicitamente — OK si el mapa default es ETF
        print(f"{f}: {count} calls")
        # Show lines with the call
        for i, line in enumerate(content.splitlines(), 1):
            if 'is_strategy_enabled' in line:
                print(f"  L{i}: {line.strip()}")
