import os
import re

dirs = ['strategies', 'strategies_crypto', 'strategies_equities']

def process_file(filepath, engine):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    if "class " not in content or "BaseStrategy" not in content:
        return

    # 1. REMOVE TOP-LEVEL GUARD
    guard_pattern = re.compile(
        r'([ \t]*)if self\.regime_manager and not self\.regime_manager\.is_strategy_enabled\([^)]+\):\n[ \t]*return\n',
        re.MULTILINE
    )
    match = guard_pattern.search(content)
    if not match:
        guard_pattern = re.compile(
            r'([ \t]*)if self\.regime_manager and not self\.regime_manager\.is_strategy_enabled\([\s\S]*?\):\n[ \t]*return\n',
            re.MULTILINE
        )
        match = guard_pattern.search(content)
        
    if not match:
        print(f"[SKIP] No guard found in {filepath}")
        return

    indent = match.group(1)
    new_content = guard_pattern.sub('', content)

    # 2. INJECT GUARD BEFORE BUY/REQUEST_BUY
    lines = new_content.split('\n')
    out_lines = []
    
    injected = False
    for line in lines:
        # Detect entry calls
        if "await self.order_manager.buy(" in line or "await self.order_manager.request_buy(" in line:
            # We want to inject BEFORE this line, with the SAME indentation
            line_indent = re.match(r'^[ \t]*', line).group(0)
            guard_line = f'{line_indent}if self.regime_manager and not self.regime_manager.is_strategy_enabled(self.STRAT_NUMBER, engine="{engine}"): return'
            out_lines.append(guard_line)
            injected = True
            
        out_lines.append(line)

    if not injected:
        print(f"[WARN] Guard removed but NOT injected in {filepath}")
    else:
        print(f"[SUCCESS] Refactored {filepath}")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out_lines))

if __name__ == '__main__':
    for d in dirs:
        engine = d.split('_')[-1] if '_' in d else 'etf'
        if engine == 'strategies': engine = 'etf'
        if not os.path.exists(d): continue
        for f in os.listdir(d):
            if f.endswith('.py') and not f.startswith('__'):
                process_file(os.path.join(d, f), engine)
