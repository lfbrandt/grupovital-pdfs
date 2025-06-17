# imagem base leve
FROM python:3.10-slim

# variáveis de ambiente para cache, configuração e encoding
ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    XDG_CACHE_HOME=/app/.cache \
    XDG_CONFIG_HOME=/app/.config \
    FLASK_ENV=production \
    PORT=5000

# define o diretório de trabalho
WORKDIR /app

# instalar dependências do sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-java-common \
      ghostscript \
      fonts-liberation \
      default-jre-headless \
      libxext6 \
      libxrender1 \
      libsm6 \
      libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# copiar e instalar dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# instalar gunicorn e python-dotenv separadamente
RUN pip install --no-cache-dir gunicorn python-dotenv

# copiar código da aplicação e ajustar permissões
COPY . .
RUN groupadd --system appuser && \
    useradd --system --gid appuser --home-dir /app --shell /sbin/nologin appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /app/uploads && \
    chown -R appuser:appuser /app/uploads

# expõe a porta do app
EXPOSE ${PORT}

# rodar como usuário não-root
USER appuser

# entrypoint para iniciar via gunicorn
ENTRYPOINT ["gunicorn", "run:app", "--bind", "0.0.0.0:${PORT}", "--workers", "4", "--timeout", "120"]