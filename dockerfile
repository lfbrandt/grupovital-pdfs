# Use a lightweight Python image
FROM python:3.10-slim

# Instala dependências de sistema: LibreOffice e Ghostscript para conversão e compressão
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice \
       ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Define diretório de trabalho
WORKDIR /app

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copia todo o código da aplicação
COPY . .

# Porta onde o Flask estará escutando
ENV PORT 5000
EXPOSE 5000

# Comando de inicialização usando gunicorn
CMD ["gunicorn", "run:app", "--bind", "0.0.0.0:${PORT}"]
