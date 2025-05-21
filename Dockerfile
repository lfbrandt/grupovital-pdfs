# imagem base leve
FROM python:3.10-slim

# define o diretório de trabalho
WORKDIR /app

# instala dependências do sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libreoffice-core libreoffice-writer libreoffice-calc \
      ghostscript default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

# copia e instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn python-dotenv

# copia o código da aplicação
COPY . .

# cria usuário de sistema sem privilégios e ajusta permissões da pasta de uploads
RUN groupadd --system appuser && \
    useradd  --system --gid appuser --home-dir /app --no-create-home --shell /sbin/nologin appuser && \
    mkdir -p /app/uploads && \
    chown -R appuser:appuser /app/uploads

# define variáveis de ambiente
ENV FLASK_ENV=production \
    PORT=5000

# expõe a porta do app
EXPOSE 5000

# passa a rodar como usuário não-root
USER appuser

# entrypoint para iniciar via gunicorn usando a porta da env
ENTRYPOINT ["sh", "-c", "gunicorn run:app --bind 0.0.0.0:${PORT} --workers 4 --timeout 120"]