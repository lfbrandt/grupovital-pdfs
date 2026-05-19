content = open('app/routes/merge.py', encoding='utf-8').read()
lines = content.split('\n')
for i, l in enumerate(lines, 1):
    stripped = l.rstrip()
    # detecta linhas que parecem ter dois statements colados
    if len(stripped) > 0 and stripped != stripped.rstrip() + '':
        pass
    # procura padrões específicos de corrupção: linha com 2+ statements
    import re
    if re.search(r'\S\s{4,}\S', stripped) and ('total_pages' in stripped or 'tmp_inputs' in stripped or 'record_job_event' in stripped or 'bytes_out' in stripped):
        print(f'{i:3}: {repr(stripped)}')
