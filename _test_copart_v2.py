"""
Testa as funcoes melhoradas de coparticipacao (Fase 1 v2).
Roda sem importar o modulo inteiro para evitar side-effects.
"""
import re, sys
import pandas as pd
from typing import List, Dict, Any, Optional

# ── replica das constantes/funcoes (mesmo codigo do modulo) ─────────────
_RE_COD_ANYWHERE = re.compile(r'\d\.\d{2}\.\d{5}')
_RE_COD_SPLIT    = re.compile(r'(?=\d\.\d{2}\.\d{5})')
_RE_MONEY_BR     = re.compile(r'R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}')
_RE_MONEY_NUM    = re.compile(r'\b\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\b')
_RE_SECAO        = re.compile(
    r'EXEMPLOS\s+DE\s+(?:EXAMES|CO(?:PARTICIPAÇÃO|PARTICIPA[CÇ][AÃ]O))'
    r'|OUTROS\s+EXEMPLOS', re.IGNORECASE)
_RE_OBS = re.compile(
    r'\*\s*Obs[:\s]|Com\s+exce[çc][aã]o\s+dos\s+valores'
    r'|referem-se\s+ao\s+custo|pode\s+haver\s+varia[çc][aã]o'
    r'|ANEXO\s+[IVX]+', re.IGNORECASE)
_HEADER_TOKENS = frozenset({
    'codigo','código','procedimento','valor unimed',
    'co-part','flex','pleno','cop.','co-participação',
    'coparticipação','20%','30%','40%','50%',
})

def _format_brl(raw):
    s = str(raw).strip()
    if _RE_MONEY_BR.match(s): return s
    s_clean = re.sub(r'^R\$\s*','',s).strip()
    if ',' in s_clean and '.' in s_clean:
        s_clean = s_clean.replace('.','').replace(',','.')
    elif ',' in s_clean:
        s_clean = s_clean.replace(',','.')
    try:
        val = float(s_clean)
        inteiro = int(val); cents = round((val-inteiro)*100)
        return f'R$ {inteiro:,}'.replace(',','.')+f',{cents:02d}'
    except: return raw

def _extract_money_values(text):
    found = _RE_MONEY_BR.findall(text)
    if found: return [_format_brl(v) for v in found]
    found_num = _RE_MONEY_NUM.findall(text)
    return [_format_brl(v) for v in found_num]

def _is_obs_line(text): return bool(_RE_OBS.search(text))
def _is_header_line(text):
    low = text.lower()
    hits = sum(1 for tok in _HEADER_TOKENS if tok in low)
    return hits >= 3 and not _RE_COD_ANYWHERE.search(text)
def _is_secao_line(text): return bool(_RE_SECAO.search(text))
def _is_total_line(text): return 'TOTAL DE COPARTICI' in text.upper()
def _row_to_text(row): return ' '.join(str(v).strip() for v in row if str(v).strip())

def _df_to_lines(df):
    lines = []
    for _, row in df.iterrows():
        raw = _row_to_text(row)
        if not raw or _is_obs_line(raw): continue
        parts = _RE_COD_SPLIT.split(raw)
        parts = [p.strip() for p in parts if p.strip()]
        lines.extend(parts)
    return lines

def _looks_like(df):
    if df is None or df.empty: return False
    text = ' '.join(str(v) for row in df.values for v in row if v is not None).upper()
    sig = 0
    if 'CO-PART' in text: sig += 2
    if 'VALOR UNIMED' in text: sig += 2
    if 'TOTAL DE COPARTICI' in text: sig += 2
    if _RE_SECAO.search(text): sig += 2
    if _RE_COD_ANYWHERE.search(text): sig += 2
    return sig >= 4

def _normalize(df):
    OUT_COLS = ['Secao','Codigo','Procedimento','Valor Unimed',
                'Copart 20%','Copart 30%','Copart 40%','Copart 50%','Tipo Linha']
    lines = _df_to_lines(df)
    rows_out: List[Dict[str,Any]] = []
    secao_atual = ''; last_proc_idx: Optional[int] = None
    for line in lines:
        if _is_obs_line(line) or _is_header_line(line): continue
        if _is_secao_line(line):
            secao_atual = line.strip(); last_proc_idx = None; continue
        if _is_total_line(line):
            ms = _extract_money_values(line)
            rows_out.append({'Secao':secao_atual,'Codigo':'','Procedimento':'Total de Coparticipação',
                'Valor Unimed':'','Copart 20%':ms[0] if ms else '','Copart 30%':ms[1] if len(ms)>1 else '',
                'Copart 40%':ms[2] if len(ms)>2 else '','Copart 50%':ms[3] if len(ms)>3 else '','Tipo Linha':'total'})
            last_proc_idx = None; continue
        m = _RE_COD_ANYWHERE.search(line)
        if m and line.strip().startswith(m.group()):
            cod = m.group(); rest = line[m.end():].strip()
            ms = _extract_money_values(rest)[:5]
            proc_text = re.sub(r'\s{2,}',' ',_RE_MONEY_NUM.sub('',_RE_MONEY_BR.sub('',rest))).strip()
            rows_out.append({'Secao':secao_atual,'Codigo':cod,'Procedimento':proc_text,
                'Valor Unimed':ms[0] if ms else '','Copart 20%':ms[1] if len(ms)>1 else '',
                'Copart 30%':ms[2] if len(ms)>2 else '','Copart 40%':ms[3] if len(ms)>3 else '',
                'Copart 50%':ms[4] if len(ms)>4 else '','Tipo Linha':'procedimento'})
            last_proc_idx = len(rows_out)-1; continue
        ms = _extract_money_values(line)
        if last_proc_idx is not None:
            r = rows_out[last_proc_idx]
            if not ms:
                r['Procedimento'] = (r['Procedimento']+' '+line.strip()).strip()
            else:
                m5 = ms[:5]
                if not r['Valor Unimed']:
                    r['Valor Unimed']=m5[0] if m5 else ''
                    r['Copart 20%']=m5[1] if len(m5)>1 else r['Copart 20%']
                    r['Copart 30%']=m5[2] if len(m5)>2 else r['Copart 30%']
                    r['Copart 40%']=m5[3] if len(m5)>3 else r['Copart 40%']
                    r['Copart 50%']=m5[4] if len(m5)>4 else r['Copart 50%']
                else:
                    cols_val = ['Copart 20%','Copart 30%','Copart 40%','Copart 50%']
                    mi = 0
                    for col in cols_val:
                        if not r[col] and mi < len(ms):
                            r[col]=ms[mi]; mi+=1
    if not rows_out: return pd.DataFrame(columns=OUT_COLS)
    result = pd.DataFrame(rows_out, columns=OUT_COLS)
    return result[(result!='').any(axis=1)]

# ── TESTE 1: dados básicos ───────────────────────────────────────────────
raw = pd.DataFrame([
    ['EXEMPLOS DE COPARTICIPACAO CLINICA GERAL','','','','','','',''],
    ['CODIGO','PROCEDIMENTO','VALOR UNIMED','CO-PART. 20%','CO-PART. 30%','CO-PART. 40%','CO-PART. 50%',''],
    ['1.01.01012','CONSULTA EM CONSULTORIO (NO HORARIO','R$ 135,00','R$ 27,00','R$ 40,50','R$ 54,00','R$ 67,50',''],
    ['','NORMAL OU PREESTABELECIDO)','','','','','',''],
    ['4.03.01583','ULTRASSONOGRAFIA OBSTETRICA','R$ 185,00','R$ 37,00','R$ 55,50','R$ 74,00','R$ 92,50',''],
    ['','Total de Coparticipacao','','R$ 43,88','R$ 65,79','R$ 87,72','R$ 109,65',''],
])
assert _looks_like(raw), "TESTE 1 FALHOU: nao detectou"
r1 = _normalize(raw)
assert len(r1) == 3, f"TESTE 1 FALHOU: esperava 3 linhas, obteve {len(r1)}"
assert r1.iloc[0]['Codigo'] == '1.01.01012'
assert 'NORMAL OU PREESTABELECIDO' in r1.iloc[0]['Procedimento'], r1.iloc[0]['Procedimento']
assert r1.iloc[0]['Valor Unimed'] == 'R$ 135,00', r1.iloc[0]['Valor Unimed']
assert r1.iloc[0]['Copart 20%']   == 'R$ 27,00',  r1.iloc[0]['Copart 20%']
assert r1.iloc[0]['Copart 50%']   == 'R$ 67,50',  r1.iloc[0]['Copart 50%']
assert r1.iloc[2]['Tipo Linha']   == 'total'
assert r1.iloc[2]['Copart 20%']   == 'R$ 43,88',  r1.iloc[2]['Copart 20%']
sys.stdout.write("TESTE 1 OK - dados basicos\n")

# ── TESTE 2: rodapé/obs deve ser ignorado ───────────────────────────────
raw2 = pd.DataFrame([
    ['EXEMPLOS DE COPARTICIPACAO CLINICA GERAL',''],
    ['CODIGO','CO-PART. 20%'],
    ['1.01.01012 CONSULTA R$ 135,00 R$ 27,00 R$ 40,50 R$ 54,00 R$ 67,50',''],
    ['*Obs: Com excecao dos valores referem-se ao custo medio',''],
    ['','Total de Coparticipacao R$ 43,88 R$ 65,79 R$ 87,72 R$ 109,65'],
])
r2 = _normalize(raw2)
obs_rows = [row for _, row in r2.iterrows()
            if 'obs' in str(row.get('Procedimento','')).lower()
            or 'excecao' in str(row.get('Procedimento','')).lower()]
assert not obs_rows, f"TESTE 2 FALHOU: rodape apareceu: {obs_rows}"
sys.stdout.write("TESTE 2 OK - rodape ignorado\n")

# ── TESTE 3: _format_brl ────────────────────────────────────────────────
assert _format_brl('135.00')   == 'R$ 135,00', _format_brl('135.00')
assert _format_brl('27,00')    == 'R$ 27,00',  _format_brl('27,00')
assert _format_brl('R$ 67,50') == 'R$ 67,50',  _format_brl('R$ 67,50')
assert _format_brl('1.350,00') == 'R$ 1.350,00', _format_brl('1.350,00')
sys.stdout.write("TESTE 3 OK - _format_brl\n")

# ── TESTE 4: célula gigante com múltiplos códigos ───────────────────────
raw4 = pd.DataFrame([
    ['EXEMPLOS DE COPARTICIPACAO CLINICA GERAL',''],
    ['CODIGO PROCEDIMENTO VALOR UNIMED CO-PART. 20% CO-PART. 30%',''],
    ['1.01.01012 CONSULTA R$ 135,00 R$ 27,00 R$ 40,50 R$ 54,00 R$ 67,50 '
     '4.03.01583 ULTRASSOM R$ 185,00 R$ 37,00 R$ 55,50 R$ 74,00 R$ 92,50',''],
])
r4 = _normalize(raw4)
assert len(r4) == 2, f"TESTE 4 FALHOU: esperava 2 linhas, obteve {len(r4)}\n{r4}"
assert r4.iloc[0]['Codigo'] == '1.01.01012'
assert r4.iloc[1]['Codigo'] == '4.03.01583'
sys.stdout.write("TESTE 4 OK - celula gigante dividida\n")

# ── TESTE 5: linha de total sem coluna Valor Unimed ─────────────────────
raw5 = pd.DataFrame([
    ['EXEMPLOS DE COPARTICIPACAO',''],
    ['','Total de Coparticipacao R$ 43,88 R$ 65,79 R$ 87,72 R$ 109,65'],
])
r5 = _normalize(raw5)
total_rows = r5[r5['Tipo Linha']=='total']
assert len(total_rows) == 1
assert total_rows.iloc[0]['Valor Unimed'] == '', "TESTE 5 FALHOU: Valor Unimed deve ser vazio no total"
assert total_rows.iloc[0]['Copart 20%'] == 'R$ 43,88'
sys.stdout.write("TESTE 5 OK - total sem Valor Unimed\n")

# ── Exibe resultado final do teste 1 para conferência visual ─────────────
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 220)
pd.set_option('display.max_colwidth', 50)
sys.stdout.write("\n--- Resultado visual (Teste 1) ---\n")
sys.stdout.write(r1.to_string(index=False) + "\n")
sys.stdout.write("\nTodos os testes passaram OK\n")
sys.stdout.flush()
