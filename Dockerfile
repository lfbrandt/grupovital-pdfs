# Use uma imagem Python leve
FROM python:3.10-slim

# Instala dependências de sistema necessárias:
# - LibreOffice core, writer e calc para conversão de documentos e planilhas
# - Ghostscript para compressão de PDFs
# - Java (default-jre-headless) para tabula-py
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      ghostscript \
      default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

# Define diretório de trabalho
WORKDIR /app

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copia todo o código da aplicação
COPY . .

# Configura porta usando formato de ENV key=value
ENV PORT=5000
EXPOSE 5000

# Inicia o app com Gunicorn, ligando à porta definida em PORT e aumentando timeout para 120s
CMD ["sh", "-c", "gunicorn run:app --bind 0.0.0.0:${PORT} --timeout 120"]