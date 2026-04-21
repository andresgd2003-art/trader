import os
import re

patterns = {
    'Telegram Bot Token': r'[0-9]{9,}:[a-zA-Z0-9_-]{35}',
    'Brave API Key': r'BSA[0-9a-zA-Z]{40,}',
    'Alpaca API Key': r'PK[A-Z0-9]{18,}',
    'Alpaca Secret Key': r'[a-zA-Z0-9]{30,}',
    'VPS Password': r'ANDYmasPRO\.98',
    'OpenAI/Anthropic': r'(sk-[a-zA-Z0-9]{48,})',
}

found = False
for root, dirs, files in os.walk('.'):
    if '.git' in root or 'venv' in root or '__pycache__' in root:
        continue
    for file in files:
        if file.endswith('.py') or file.endswith('.txt') or file.endswith('.md') or file.endswith('.json'):
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    for name, pat in patterns.items():
                        matches = re.finditer(pat, content)
                        for match in matches:
                            print(f"[!] {name} expuesto en {filepath}: {match.group(0)}")
                            found = True
            except Exception as e:
                pass
if not found:
    print("Sin coincidencias iniciales detectadas por regex.")
