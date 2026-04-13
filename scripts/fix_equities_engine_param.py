"""
Script de un solo uso: corrige el argumento engine='equities' faltante
en todas las estrategias del motor de acciones.
"""
import os

folder = os.path.join(os.path.dirname(__file__), '..', 'strategies_equities')
old = "regime_manager.is_strategy_enabled(self.STRAT_NUMBER)"
new = "regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine='equities')"

fixed = []
for f in sorted(os.listdir(folder)):
    if f.endswith('.py') and f != '__init__.py':
        path = os.path.join(folder, f)
        with open(path, encoding='utf-8') as fh:
            content = fh.read()
        if old in content:
            content = content.replace(old, new)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(content)
            fixed.append(f)

print(f"Archivos corregidos ({len(fixed)}): {fixed}")
