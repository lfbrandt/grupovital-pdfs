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

3. Instale as dependências:
```bash
pip install -r requirements.txt
```
4. Crie um arquivo `.env` na raiz do projeto (opcionalmente copie `.env.example`):
```bash
cp .env.example .env
```

---

## ▶️ Como Executar

```bash
python run.py
```

Acesse no navegador:

```
http://localhost:5000
```

---

## 📁 Estrutura do Projeto

```
/routes         # Rotas das funcionalidades (convert, merge, split, compress)
/services       # Lógica dos serviços (PyPDF2, LibreOffice, etc.)
/templates      # HTML das páginas
/static         # CSS, JS e imagens
/uploads        # Arquivos enviados temporariamente
run.py          # Inicializa a aplicação
requirements.txt# Bibliotecas necessárias
```

---

## 🤝 Contribuindo

Contribuições são bem-vindas!  
Você pode abrir **issues**, enviar sugestões ou criar **pull requests**.

---

## 📄 Licença

Este projeto está sob a licença MIT.  
Feito por Luis Brandt.
