"""
Script de un solo uso: verifica y corrige el argumento engine='crypto' faltante
en las estrategias del motor de cripto.
"""
import os

folder = os.path.join(os.path.dirname(__file__), '..', 'strategies_crypto')
old = "regime_manager.is_strategy_enabled(self.STRAT_NUMBER)"
new_template = "regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='crypto')"

strat_num_map = {
    'strat_02_bollinger_vb.py': 2,
    'strat_06_volume_anomaly.py': 6,
    'strat_07_pair_div.py': 7,
    'strat_09_vwap.py': 9,
    'strat_10_sentiment_fg.py': 10,
}

fixed = []
for f in sorted(os.listdir(folder)):
    if f.endswith('.py') and f != '__init__.py':
        path = os.path.join(folder, f)
        with open(path, encoding='utf-8') as fh:
            content = fh.read()
        if old in content:
            # Use strat number from map, or generic
            num = strat_num_map.get(f, None)
            if num:
                specific_old = f"regime_manager.is_strategy_enabled({num})"
                specific_new = f"regime_manager.is_strategy_enabled({num}, engine='crypto')"
                if specific_old in content:
                    content = content.replace(specific_old, specific_new)
                else:
                    content = content.replace(old, new_template)
            else:
                content = content.replace(old, new_template)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(content)
            fixed.append(f)
        # Also check for hardcoded number without engine
        for num in range(1, 11):
            old_num = f"regime_manager.is_strategy_enabled({num})"
            new_num = f"regime_manager.is_strategy_enabled({num}, engine='crypto')"
            if old_num in content and "engine='crypto'" not in content:
                content = content.replace(old_num, new_num)
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(content)
                if f not in fixed:
                    fixed.append(f)

print(f"Archivos corregidos ({len(fixed)}): {fixed}")

# Verify all are clean now
print("\n--- Verificacion ---")
for f in sorted(os.listdir(folder)):
    if f.endswith('.py') and f != '__init__.py':
        path = os.path.join(folder, f)
        content = open(path, encoding='utf-8').read()
        count = content.count('is_strategy_enabled')
        correct = content.count("engine='crypto'")
        print(f"{f}: {count} calls, {correct} with engine param -> {'OK' if count == correct else 'MISSING ENGINE PARAM!'}")
