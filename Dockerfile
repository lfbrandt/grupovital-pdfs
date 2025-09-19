# imagem base leve
FROM python:3.10-slim

# variáveis de ambiente para cache, configuração e encoding
ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    XDG_CACHE_HOME=/app/.cache \
    XDG_CONFIG_HOME=/app/.config \
    FLASK_ENV=production \
    PORT=5000 \
    FORCE_HTTPS=1 \
    SOFFICE_BIN=/usr/bin/soffice \
    GHOSTSCRIPT_BIN=/usr/bin/gs \
    PIP_NO_CACHE_DIR=1

# define o diretório de trabalho
WORKDIR /app

# ----- Sistema: LibreOffice, Ghostscript, OCR, libmagic e fontes -----
# - libmagic1: python-magic (MIME real)
# - qpdf, tesseract e pt-br: OCRmyPDF
# - fonts-dejavu/noto: melhora fidelidade de conversão no LibreOffice
# - libs X básicas para renderizações headless
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libreoffice-core \
      libreoffice-writer \
      libreoffice-calc \
      libreoffice-java-common \
      ghostscript \
      qpdf \
      tesseract-ocr \
      tesseract-ocr-por \
      libmagic1 \
      fonts-liberation \
      fonts-dejavu \
      fonts-noto-core \
      fonts-noto-cjk \
      default-jre-headless \
      libxext6 \
      libxrender1 \
      libsm6 \
      libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# ----- Python: atualizar instalador e instalar dependências -----
# 1) upgrade do pip/setuptools/wheel para resolver bem as versões
RUN python -m pip install --upgrade pip setuptools wheel

# 2) instalar requirements (no Py 3.10 use numpy==1.26.4)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) gunicorn
RUN pip install --no-cache-dir gunicorn

# ----- Código da aplicação -----
# Copiamos já com dono correto para evitar chown extra
COPY --chown=65534:65534 . .

# garantir diretório de uploads
RUN mkdir -p /app/uploads

# rodar como usuário não-root (nobody/nogroup)
USER 65534:65534

# expõe a porta do app
EXPOSE ${PORT}

# entrypoint para iniciar via gunicorn
# - forwarded-allow-ips: respeitar X-Forwarded-* do proxy (Nginx/Traefik)
# - worker-tmp-dir: usa tmp em memória p/ PDFs grandes
# - graceful-timeout: janela para shutdown limpo em jobs longos
ENTRYPOINT ["sh","-c","gunicorn run:app --bind 0.0.0.0:${PORT} --workers 4 --timeout 120 --graceful-timeout 30 --worker-tmp-dir /dev/shm --forwarded-allow-ips='*'"]