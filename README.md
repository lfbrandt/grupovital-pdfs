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
# Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate
```

3. Instale as dependências (incluindo `python-dotenv`):
```bash
pip install -r requirements.txt
```
4. Os arquivos `envs/.env.development` e `envs/.env.testing` já estão no repositório.
   Edite-os conforme necessário (copie `.env.example` se algum estiver faltando).
5. Em cada arquivo de ambiente, defina `SECRET_KEY` com um valor aleatório. Sem essa chave o app exibirá erros de CSRF.
6. (Opcional) Defina `LIBREOFFICE_BIN` ou `GHOSTSCRIPT_BIN` caso os executáveis
   não estejam no seu `PATH`.

### Variáveis de ambiente

- **`LIBREOFFICE_BIN`**: caminho para o executável do LibreOffice (`soffice`).
- **`GHOSTSCRIPT_BIN`**: caminho para o executável do Ghostscript.
- **`FORCE_HTTPS`**: define se o Flask-Talisman deve forçar HTTPS (`true` ou `false`).
  Padrão `true`.

Se não definidas, o aplicativo utiliza `libreoffice` e `gs` (Linux) ou os
caminhos padrão do Windows.

---

## ▶️ Como Executar


Defina `FLASK_ENV` para escolher qual arquivo em `envs/` deve ser carregado (por padrão `development`).
Para rodar em modo de testes:
```bash
source venv-test/bin/activate
export FLASK_ENV=testing
python run.py
```

Acesse no navegador:

```
http://localhost:5000
```

## 🧪 Testes

Certifique-se de que todas as dependências estejam instaladas:

```bash
pip install -r requirements.txt
```

Para rodar a suíte de testes:

```bash
source venv-test/bin/activate
export FLASK_ENV=testing
pytest -q
```

---

## 📁 Estrutura do Projeto

```
/app/routes         # Rotas das funcionalidades (convert, merge, split, compress)
/app/services       # Lógica dos serviços (PyPDF2, LibreOffice, etc.)
/app/templates      # HTML das páginas
/app/static         # CSS, JS e imagens
/uploads            # Arquivos enviados temporariamente
run.py              # Inicializa a aplicação
requirements.txt    # Bibliotecas necessárias
```

---

## 🤝 Contribuindo

Contribuições são bem-vindas!  
Você pode abrir **issues**, enviar sugestões ou criar **pull requests**.

---

## 📄 Licença

Este projeto está licenciado sob a [Licença MIT](LICENSE).  
Feito por Luis Brandt.
