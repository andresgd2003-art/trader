import os

folder = r'c:\Users\user\OneDrive\Escritorio\gemini cli\trader'
groups = {
    'ETF':      'strategies',
    'CRYPTO':   'strategies_crypto',
    'EQUITIES': 'strategies_equities',
}

for group_name, subfolder in groups.items():
    path = os.path.join(folder, subfolder)
    print(f'=== {group_name} ===')
    for f in sorted(os.listdir(path)):
        if f.endswith('.py') and not f.startswith('__'):
            fpath = os.path.join(path, f)
            lines = open(fpath, encoding='utf-8').readlines()
            strat_num = ''
            doclines = []
            in_doc = False
            for l in lines[:50]:
                if 'STRAT_NUMBER' in l:
                    strat_num = l.strip()
                if '"""' in l:
                    if not in_doc:
                        in_doc = True
                    else:
                        in_doc = False
                        break
                elif in_doc:
                    doclines.append(l.rstrip())
            doc = ' | '.join(l.strip() for l in doclines if l.strip())[:300]
            print(f'  [{strat_num}] {f}: {doc}')
    print()
