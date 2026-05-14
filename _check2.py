import ast, sys

files = [
    'app/services/converter_service.py',
    'app/routes/converter.py',
    'app/routes/viewer.py',
]
BAD = [
    'writer.close()    if',
    'wrote_any = True    writer',
    'idx += 1                    wrote',
    'header_idx].values',   # uso antigo de iloc[label]
]
ok = True
for f in files:
    with open(f, encoding='utf-8') as fh:
        src = fh.read()
    for bad in BAD:
        if bad in src:
            print(f'[FAIL] linha colada/padrão antigo em {f!r}: {bad!r}')
            ok = False
    try:
        ast.parse(src)
        print(f'[OK]   {f}')
    except SyntaxError as e:
        print(f'[FAIL] SyntaxError em {f}: {e}')
        ok = False
sys.exit(0 if ok else 1)
