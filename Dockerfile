# Use uma imagem Python leve
FROM python:3.10-slim

# Definir diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libreoffice-core libreoffice-writer libreoffice-calc \
      ghostscript default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn python-dotenv

# Copiar todo o código da aplicação
COPY . .

# Definir variáveis de ambiente para produção
ENV FLASK_ENV=production PORT=5000

# Expor porta padrão da aplicação
EXPOSE 5000

# Criar usuário de sistema sem privilégios para rodar o app
RUN groupadd --system appuser && \
    useradd --system --gid appuser --home-dir /app --no-create-home --shell /sbin/nologin appuser

# Mudar para o usuário não-root
USER appuser

# Iniciar o app via Gunicorn, expandindo variável de ambiente PORT
ENTRYPOINT ["sh", "-c", "gunicorn run:app --bind 0.0.0.0:${PORT} --workers 4 --timeout 120"]