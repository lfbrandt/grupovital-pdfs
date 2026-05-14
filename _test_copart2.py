"""
Testa _looks_like_coparticipacao_table e _normalize_coparticipacao_table
copiando APENAS as funcoes necessarias para evitar side-effects do modulo.
"""
import re
import sys
import pandas as pd

# ── copias minimas das funcoes (sem importar o modulo inteiro) ──────────
_RE_COD   = re.compile(r'^\d\.\d{2}\.\d{5}')
_RE_MONEY = re.compile(r'R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}')
_RE_SECAO = re.compile(
    r'EXEMPLOS\s+DE\s+CO(?:PARTICIPAÇÃO|PARTICIPA[CÇ][AÃ]O)|OUTROS\s+EXEMPLOS',
    re.IGNORECASE,
)
_HEADER_TOKENS = {
    'codigo', 'código', 'procedimento', 'valor unimed',
    'co-part', 'flex', 'pleno', 'cop.', 'co-participação',
}

def _looks_like(df):
    if df is None or df.empty:
        return False
    text = ' '.join(str(v) for row in df.values for v in row if v is not None).upper()
    signals = 0
    if 'CO-PART' in text: signals += 2
    if 'VALOR UNIMED' in text: signals += 2
    if 'TOTAL DE COPARTICI' in text: signals += 2
    if _RE_SECAO.search(text): signals += 2
    if _RE_COD.search(' '.join(str(v) for row in df.values for v in row if v)): signals += 2
    return signals >= 4

def _normalize(df):
    OUT_COLS = ['Secao','Codigo','Procedimento','Valor Unimed',
                'Copart 20%','Copart 30%','Copart 40%','Copart 50%','Tipo Linha']
    rows_out = []
    secao_atual = ''
    last_proc_idx = None

    def cells(row):
        return [str(v).strip() for v in row if str(v).strip()]

    def moneys(cs):
        vals = []
        for c in cs:
            vals.extend(_RE_MONEY.findall(c))
        return vals

    def is_header(cs):
        j = ' '.join(cs).lower()
        return sum(1 for t in _HEADER_TOKENS if t in j) >= 2

    def is_secao(cs):
        return bool(_RE_SECAO.search(' '.join(cs)))

    def is_total(cs):
        return 'TOTAL DE COPARTICI' in ' '.join(cs).upper()

    def is_proc(cs):
        return bool(cs) and bool(_RE_COD.match(cs[0]))

    for _, row in df.iterrows():
        cs = cells(row)
        if not cs: continue
        if is_header(cs): continue
        if is_secao(cs):
            secao_atual = ' '.join(cs); last_proc_idx = None; continue
        if is_total(cs):
            ms = moneys(cs)
            rows_out.append({'Secao':secao_atual,'Codigo':'','Procedimento':'Total de Coparticipação',
                'Valor Unimed':'','Copart 20%':ms[0] if ms else '','Copart 30%':ms[1] if len(ms)>1 else '',
                'Copart 40%':ms[2] if len(ms)>2 else '','Copart 50%':ms[3] if len(ms)>3 else '','Tipo Linha':'total'})
            last_proc_idx = None; continue
        if is_proc(cs):
            ms = moneys(cs)
            text_cs = [c for c in cs[1:] if not _RE_MONEY.search(c) and not _RE_COD.match(c)]
            rows_out.append({'Secao':secao_atual,'Codigo':cs[0],'Procedimento':' '.join(text_cs),
                'Valor Unimed':ms[0] if ms else '','Copart 20%':ms[1] if len(ms)>1 else '',
                'Copart 30%':ms[2] if len(ms)>2 else '','Copart 40%':ms[3] if len(ms)>3 else '',
                'Copart 50%':ms[4] if len(ms)>4 else '','Tipo Linha':'procedimento'})
            last_proc_idx = len(rows_out)-1; continue
        has_money = bool(_RE_MONEY.search(' '.join(cs)))
        if not has_money and last_proc_idx is not None:
            rows_out[last_proc_idx]['Procedimento'] = (rows_out[last_proc_idx]['Procedimento']+' '+' '.join(cs)).strip()
        elif has_money and last_proc_idx is not None:
            ms = moneys(cs); r = rows_out[last_proc_idx]
            if not r['Valor Unimed'] and ms:
                r['Valor Unimed']=ms[0]; r['Copart 20%']=ms[1] if len(ms)>1 else r['Copart 20%']
                r['Copart 30%']=ms[2] if len(ms)>2 else r['Copart 30%']
                r['Copart 40%']=ms[3] if len(ms)>3 else r['Copart 40%']
                r['Copart 50%']=ms[4] if len(ms)>4 else r['Copart 50%']
    if not rows_out:
        return pd.DataFrame(columns=OUT_COLS)
    result = pd.DataFrame(rows_out, columns=OUT_COLS)
    return result[(result != '').any(axis=1)]

# ── dados de teste ──────────────────────────────────────────────────────
raw = pd.DataFrame([
    ['EXEMPLOS DE COPARTICIPACAO CLINICA GERAL','','','','','','',''],
    ['CODIGO','PROCEDIMENTO','VALOR UNIMED','CO-PART. 20%','CO-PART. 30%','CO-PART. 40%','CO-PART. 50%',''],
    ['1.01.01012','CONSULTA EM CONSULTORIO (NO HORARIO','R$ 135,00','R$ 27,00','R$ 40,50','R$ 54,00','R$ 67,50',''],
    ['','NORMAL OU PREESTABELECIDO)','','','','','',''],
    ['4.03.01583','ULTRASSONOGRAFIA OBSTETRICA','R$ 185,00','R$ 37,00','R$ 55,50','R$ 74,00','R$ 92,50',''],
    ['','Total de Coparticipacao','','R$ 43,88','R$ 65,79','R$ 87,72','R$ 109,65',''],
])

detected = _looks_like(raw)
result   = _normalize(raw)

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 220)
pd.set_option('display.max_colwidth', 55)
sys.stdout.write("Detectado: " + str(detected) + "\n")
sys.stdout.write(result.to_string(index=False) + "\n")

# Asserts
assert detected, "FALHOU: nao detectou tabela"
assert len(result) == 3, f"FALHOU: esperava 3 linhas, obteve {len(result)}"
assert result.iloc[0]['Codigo'] == '1.01.01012'
assert 'NORMAL OU PREESTABELECIDO' in result.iloc[0]['Procedimento'], result.iloc[0]['Procedimento']
assert result.iloc[1]['Codigo'] == '4.03.01583'
assert result.iloc[2]['Tipo Linha'] == 'total'
assert result.iloc[2]['Copart 20%'] == 'R$ 43,88', result.iloc[2]['Copart 20%']
sys.stdout.write("\nTodos os testes passaram OK\n")
sys.stdout.flush()
