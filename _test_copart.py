import pandas as pd
from app.services.converter_service import (
    _looks_like_coparticipacao_table,
    _normalize_coparticipacao_table,
)

# Simula saída bruta do camelot para o PDF de teste
raw = pd.DataFrame([
    ['EXEMPLOS DE COPARTICIPACAO CLINICA GERAL', '', '', '', '', '', '', ''],
    ['CODIGO', 'PROCEDIMENTO', 'VALOR UNIMED', 'CO-PART. 20%', 'CO-PART. 30%', 'CO-PART. 40%', 'CO-PART. 50%', ''],
    ['1.01.01012', 'CONSULTA EM CONSULTORIO (NO HORARIO', 'R$ 135,00', 'R$ 27,00', 'R$ 40,50', 'R$ 54,00', 'R$ 67,50', ''],
    ['', 'NORMAL OU PREESTABELECIDO)', '', '', '', '', '', ''],
    ['4.03.01583', 'ULTRASSONOGRAFIA OBSTETRICA', 'R$ 185,00', 'R$ 37,00', 'R$ 55,50', 'R$ 74,00', 'R$ 92,50', ''],
    ['', 'Total de Coparticipacao', '', 'R$ 43,88', 'R$ 65,79', 'R$ 87,72', 'R$ 109,65', ''],
])

detected = _looks_like_coparticipacao_table(raw)
print("Detectado:", detected)

result = _normalize_coparticipacao_table(raw)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)
pd.set_option('display.max_colwidth', 60)
print(result.to_string(index=False))

# Verificações
assert detected, "FALHOU: deveria detectar tabela de coparticipação"
assert len(result) == 3, f"FALHOU: esperava 3 linhas, obteve {len(result)}"
assert result.iloc[0]['Codigo'] == '1.01.01012', "FALHOU: código errado na linha 0"
assert 'NORMAL OU PREESTABELECIDO' in result.iloc[0]['Procedimento'], "FALHOU: continuação não foi concatenada"
assert result.iloc[1]['Codigo'] == '4.03.01583', "FALHOU: código errado na linha 1"
assert result.iloc[2]['Tipo Linha'] == 'total', "FALHOU: linha de total não detectada"
assert result.iloc[2]['Copart 20%'] == 'R$ 43,88', f"FALHOU: valor total errado: {result.iloc[2]['Copart 20%']}"

print("\nTodos os testes passaram OK")
