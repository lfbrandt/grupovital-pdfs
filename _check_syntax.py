import ast, sys

files = [
    'app/routes/viewer.py',
    'app/services/converter_service.py',
    'app/routes/converter.py',
]

BAD_PATTERNS = [
    'writer.close()    if',
    'wrote_any = True    writer',
    'idx += 1                    wrote',
]

ok = True
for f in files:
    with open(f, encoding='utf-8') as fh:
        src = fh.read()
    for bad in BAD_PATTERNS:
        if bad in src:
            print(f'[FAIL] LINHA COLADA em {f!r}: {bad!r}')
            ok = False
    try:
        ast.parse(src)
        print(f'[OK]   AST valido: {f}')
    except SyntaxError as e:
        print(f'[FAIL] SyntaxError em {f}: {e}')
        ok = False

sys.exit(0 if ok else 1)
