# Use uma imagem Python leve
FROM python:3.10-slim

# Instala dependências de sistema necessárias:
# - fonts-liberation: fontes para renderização de PDFs
# - libreoffice-core, writer e calc: núcleo mínimo do LibreOffice para conversão de documentos e planilhas
# - ghostscript: compressão de PDFs
# - openjdk-11-jre-headless: runtime Java para tabula-py
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      fonts-liberation \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      ghostscript \
      openjdk-11-jre-headless && \
    rm -rf /var/lib/apt/lists/*

# Define diretório de trabalho
WORKDIR /app

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copia todo o código da aplicação
COPY . .

# Porta em que o Flask vai escutar
ENV PORT 5000
EXPOSE 5000

# Comando de inicialização usando Gunicorn, ligando à porta definida em PORT
CMD ["sh", "-c", "gunicorn run:app --bind 0.0.0.0:${PORT}"]