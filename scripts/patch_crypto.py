import re
from pathlib import Path

strats_dir = Path(r'c:\Users\user\OneDrive\Escritorio\gemini cli\trader\strategies_crypto')
targets = [
    'strat_04_smart_twap.py',
    'strat_05_funding_squeeze.py',
    'strat_06_vol_anomaly.py',
    'strat_07_pair_divergence.py',
    'strat_08_ema_ribbon.py',
    'strat_09_vwap_touch.py',
]

GUARD_TEMPLATE = "        if self.regime_manager and not self.regime_manager.is_strategy_enabled({num}, engine='crypto'):\n            return\n"

for fname in targets:
    fp = strats_dir / fname
    content = fp.read_text(encoding='utf-8')
    num = int(fname.split('_')[1])
    guard = GUARD_TEMPLATE.format(num=num)

    # Already patched check
    if "is_strategy_enabled" in content:
        print(f"  SKIP (already patched): {fname}")
        continue

    # Try to find on_bar and insert guard right after the def line
    lines = content.split('\n')
    patched_lines = []
    inserted = False
    i = 0
    while i < len(lines):
        line = lines[i]
        patched_lines.append(line)
        # Look for the on_bar definition line
        if not inserted and re.match(r'\s+async def on_bar\(self, bar\)', line):
            # Find the first non-empty line inside the body
            i += 1
            # Skip docstring or blank lines
            while i < len(lines) and (lines[i].strip() == '' or lines[i].strip().startswith('"""') or lines[i].strip().startswith("'''")):
                patched_lines.append(lines[i])
                i += 1
            # Now insert the guard
            for guard_line in guard.split('\n'):
                if guard_line or guard_line == '':
                    patched_lines.append(guard_line)
            inserted = True
            continue
        i += 1

    if inserted:
        fp.write_text('\n'.join(patched_lines), encoding='utf-8')
        print(f"  OK: {fname}")
    else:
        print(f"  FAILED (no on_bar found): {fname}")
        for j, l in enumerate(lines):
            if 'on_bar' in l:
                print(f"    Line {j}: {repr(l)}")
