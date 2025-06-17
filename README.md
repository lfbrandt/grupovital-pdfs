# 🧩 Grupo Vital PDFs

Uma plataforma web simples e funcional para **converter, dividir, juntar e comprimir PDFs**. Compatível com Windows e Linux, sem necessidade de instalação para o usuário final — tudo via navegador!

---

## ✅ Funcionalidades

- ✅ Conversão de documentos (DOCX, ODT, JPG, PNG, etc) para PDF  
- ✅ Junção de vários arquivos PDF em um só  
- ✅ Divisão de arquivos PDF em páginas separadas  
- ✅ Compressão de PDFs para reduzir o tamanho  
- ✅ Interface leve, responsiva e fácil de usar  

---

## ⚙️ Requisitos para desenvolvimento

> O usuário final **não precisa instalar nada**.  
> Estes requisitos são **apenas para quem vai rodar o projeto localmente** (ex: você, devs ou colaboradores).

- Python 3.9 ou superior  
- LibreOffice instalado (para conversão de documentos)  
- Ghostscript instalado (para compressão de PDFs)  

### Instalação no Linux:

```bash
sudo apt install libreoffice ghostscript
```

### Instalação no Windows:

- [Baixe o LibreOffice](https://www.libreoffice.org/download/download/)  
- [Baixe o Ghostscript](https://www.ghostscript.com/download/gsdnld.html)  

---

## 🛠️ Instalação do Projeto

1. Clone este repositório:
   ```bash
   git clone https://github.com/lfbrandt/grupovital-pdfs.git
   cd grupovital-pdfs
   ```

2. Crie e ative um ambiente virtual:
   ```bash
   python -m venv venv
   # Linux/macOS:
   source venv/bin/activate
   # Windows (CMD):
   venv\Scripts\activate.bat
   # Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   ```

3. Instale as dependências (incluindo `python-dotenv`):
   ```bash
   pip install -r requirements.txt
   ```

4. Os arquivos `envs/.env.development` e `envs/.env.testing` já estão no repositório.  
   Copie de `.env.example` se algum estiver faltando e ajuste as variáveis.

5. Em cada arquivo de ambiente, defina `SECRET_KEY` com um valor aleatório.  
   Sem essa chave o app exibirá erros de CSRF.

6. (Opcional) Defina `LIBREOFFICE_BIN` ou `GHOSTSCRIPT_BIN` caso os executáveis  
   não estejam no seu `PATH`.

### Variáveis de ambiente

- **`LIBREOFFICE_BIN`**: caminho para o executável do LibreOffice (`soffice`).
- **`GHOSTSCRIPT_BIN`**: caminho para o executável do Ghostscript.
- **`FORCE_HTTPS`**: define se o Flask-Talisman deve forçar HTTPS (`true` ou `false`).
  Padrão `true`.
- **`MAX_CONTENT_LENGTH`**: limite máximo em bytes para uploads. Padrão `16777216` (16 MB).

- **`LIBREOFFICE_TIMEOUT`**: tempo limite (segundos) da chamada ao LibreOffice.
  Padrão `60` (ajuste para `120` se quiser alinhar ao timeout do Gunicorn).
- **`GHOSTSCRIPT_TIMEOUT`**: tempo limite (segundos) da chamada ao Ghostscript.
  Padrão `60`.

Se não definidas, o aplicativo utiliza `libreoffice` e `gs` (Linux) ou os
caminhos padrão do Windows.

### Ajustando o limite de upload

O valor de `MAX_CONTENT_LENGTH` determina o tamanho máximo permitido para
envios. Caso precise aceitar arquivos maiores, edite esse valor nos arquivos
`.env` ou defina a variável de ambiente antes de iniciar o aplicativo.
O número deve ser informado em bytes. Por exemplo, para permitir 32 MB utilize
`33554432`.

---

## ▶️ Como Executar

Defina `FLASK_ENV` para escolher qual arquivo em `envs/` deve ser carregado  
(por padrão `development`).  

Para rodar em modo de testes:
```bash
# Ativar o venv de teste
# Linux/macOS:
source venv-test/bin/activate
# Windows (CMD):
venv-test\Scripts\activate.bat
# Windows (PowerShell):
.\venv-test\Scripts\Activate.ps1

# Definir o modo de teste
# Linux/macOS:
export FLASK_ENV=testing
# Windows (CMD):
set FLASK_ENV=testing
# Windows (PowerShell):
$Env:FLASK_ENV = "testing"

# Iniciar a aplicação
python run.py
```

Acesse no navegador:
```
http://localhost:5000
```

---

## 🧪 Testes

Certifique-se de que todas as dependências estejam instaladas:
```bash
pip install -r requirements.txt
pip install pytest
```

Para rodar a suíte de testes:
```bash
# Ativar o venv de teste
# Linux/macOS:
source venv-test/bin/activate
# Windows (CMD):
venv-test\Scripts\activate.bat
# Windows (PowerShell):
.\venv-test\Scripts\Activate.ps1

# Definir o modo de teste
# Linux/macOS:
export FLASK_ENV=testing
# Windows (CMD):
set FLASK_ENV=testing
# Windows (PowerShell):
$Env:FLASK_ENV = "testing"

# Executar testes
pytest -q
```

---

## 📁 Estrutura do Projeto

```
/app/routes         # Rotas das funcionalidades (convert, merge, split, compress)
/app/services       # Lógica dos serviços (PyPDF2, LibreOffice, etc.)
/app/templates      # HTML das páginas
/app/static         # CSS, JS e imagens
/envs               # Arquivos de ambiente: .env.development, .env.testing, .env.production
/venv               # Ambiente virtual “prod” (não versionado)
/venv-test          # Ambiente virtual de teste (não versionado)
/uploads            # Arquivos enviados temporariamente (não versionado)
/tests              # Testes automatizados
run.py              # Inicializa a aplicação
requirements.txt    # Bibliotecas necessárias
README.md           # Documentação do projeto
.gitignore          # Arquivos e pastas ignorados pelo Git
LICENSE             # Licença MIT do projeto
```

> **Importante**:  
> A pasta `uploads/` **armazena arquivos temporários** gerados nas operações  
> de conversão, junção, divisão e compressão. Ela está listada em  
> `.gitignore` e **deve permanecer vazia no repositório** — assim evitamos  
> arquivos binários acidentais no controle de versão.

---

## 🤝 Contribuindo

Contribuições são bem-vindas!  
Você pode abrir **issues**, enviar sugestões ou criar **pull requests**.

---

## 📄 Licença

Este projeto está licenciado sob a [Licença MIT](LICENSE).  
Feito por Luis Brandt.
