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
- Node.js e npm (para compilar o CSS)

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

4. Crie a pasta `envs/` (caso ainda não exista) e copie `.env.example` para
   `envs/.env.development` e `envs/.env.testing`.
   **Nunca adicione esses arquivos ao Git com valores reais de `SECRET_KEY`.**

5. Em cada arquivo de ambiente, defina `SECRET_KEY` com um valor aleatório.
   Sem essa chave o app exibirá erros de CSRF.

6. (Opcional) Defina `LIBREOFFICE_BIN` ou `GHOSTSCRIPT_BIN` caso os executáveis  
   não estejam no seu `PATH`.

### Variáveis de ambiente

- **`SECRET_KEY`**: chave secreta do Flask para proteção de formulários.
- **`HOST`**: endereço que a aplicação irá escutar. Padrão `0.0.0.0`.
- **`PORT`**: porta do servidor. Padrão `5000`.
- **`LIBREOFFICE_BIN`**: caminho para o executável do LibreOffice (`soffice`).
- **`GHOSTSCRIPT_BIN`**: caminho para o executável do Ghostscript.
- **`FORCE_HTTPS`**: define se o Flask-Talisman deve forçar HTTPS (`true` ou `false`).
  Padrão `true`.
- **`MAX_CONTENT_LENGTH`**: limite máximo em bytes para uploads. Padrão `16777216` (16 MB).

- **`LIBREOFFICE_TIMEOUT`**: tempo limite (segundos) da chamada ao LibreOffice.
  Padrão `120` (ajuste para `120` se quiser alinhar ao timeout do Gunicorn).
- **`GHOSTSCRIPT_TIMEOUT`**: tempo limite (segundos) da chamada ao Ghostscript.
  Padrão `120`.

Se não definidas, o aplicativo utiliza `libreoffice` e `gs` (Linux) ou os
caminhos padrão do Windows.

### Ajustando o limite de upload

O valor de `MAX_CONTENT_LENGTH` determina o tamanho máximo permitido para
envios. Caso precise aceitar arquivos maiores, edite esse valor nos arquivos
`.env` ou defina a variável de ambiente antes de iniciar o aplicativo.
O número deve ser informado em bytes. Por exemplo, para permitir 32 MB utilize
`33554432`.

## 🎨 Compilar o CSS

Após instalar as dependências, use o Sass para gerar o CSS:

```bash
npm install
npm run build
```

Para recompilar automaticamente durante o desenvolvimento:
```bash
npm run watch
```


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

### Rodando com Docker

Se preferir, é possível executar o projeto usando Docker:

```bash
# construir a imagem
docker build -t grupovital-pdfs .

# executar expondo a porta desejada
docker run -p 5000:5000 --env-file envs/.env.development -e FLASK_ENV=development grupovital-pdfs
```

A opção `-e FLASK_ENV=development` garante que o Flask carregue o arquivo de
ambiente correspondente.

Depois acesse `http://localhost:5000` no navegador.

### 👀 Pré-visualizar arquivos

Antes de finalizar qualquer operação – como converter, juntar ou dividir PDFs – é possível conferir os arquivos enviados. Após adicioná-los à lista, clique no botão **Preview** (próximo ao botão principal da página). Um modal será aberto com:

- Lista dos arquivos selecionados;
- Visualização do PDF à direita quando houver um;
- Ações de **Girar** e **Recortar** para cada item;
- Botões para confirmar ou cancelar o processamento.

Assim você ajusta o documento antes de efetivar a tarefa desejada.

### Selecionar páginas específicas

Ao juntar ou dividir PDFs, clique sobre cada miniatura para marcar as páginas
que deseja manter. Utilize o botão "×" em cima da miniatura para removê-la da
visualização. A lista de páginas selecionadas é enviada para o servidor no campo
`pagesMap`.

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
/envs               # Arquivos de ambiente (copiados de .env.example, nunca versionados)
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
>
> Nunca versione os arquivos de ambiente em `envs/` com valores reais de
> `SECRET_KEY` ou outras credenciais.

## 🛠 Solução de Problemas

Caso o botão **Converter Todos** não faça nenhuma requisição e o console do
navegador exiba erros de JavaScript, verifique a função `adicionarArquivo` em
`script.js`. Um erro comum é digitar:

```javascript
arquivosSelecionados.push(.novosArquivos);
```

O correto é utilizar o operador _spread_ para inserir os novos arquivos:

```javascript
arquivosSelecionados.push(...novosArquivos);
```

Depois de corrigir, recarregue a página (Ctrl+F5) e confirme que não há erros
no console.

---

## 🤝 Contribuindo

Contribuições são bem-vindas!  
Você pode abrir **issues**, enviar sugestões ou criar **pull requests**.

---

## 📄 Licença

Este projeto está licenciado sob a [Licença MIT](LICENSE).  
Feito por Luis Brandt.
