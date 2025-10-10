# imagem base leve
FROM python:3.10-slim

# variáveis de ambiente
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    XDG_CACHE_HOME=/app/.cache \
    XDG_CONFIG_HOME=/app/.config \
    FLASK_ENV=production \
    PORT=5000 \
    FORCE_HTTPS=1 \
    SOFFICE_BIN=/usr/bin/soffice \
    GHOSTSCRIPT_BIN=/usr/bin/gs \
    PIP_NO_CACHE_DIR=1 \
    WORKERS=2 \
    THREADS=8 \
    TIMEOUT=120 \
    # Defaults do OCR (podem ser sobrescritos no ambiente)
    OCR_BIN=ocrmypdf \
    OCR_LANGS=por+eng \
    OCR_TIMEOUT=300 \
    OCR_MEM_MB=1024 \
    OCR_JOBS=1

# diretório de trabalho
WORKDIR /app

# ----- Sistema: LibreOffice, Ghostscript, QPDF, OCR deps, libmagic, fontes, libs X, tini -----
#  - libmagic1 + file: python-magic (MIME real)
#  - tesseract-ocr + pt-br: OCR
#  - qpdf/ghostscript: exigidos por ocrmypdf
#  - fontes: fidelidade no LibreOffice
#  - tini: init correto (sinais/zumbis)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      libreoffice-core libreoffice-writer libreoffice-calc libreoffice-java-common \
      default-jre-headless \
      ghostscript qpdf \
      tesseract-ocr tesseract-ocr-por \
      libmagic1 file \
      fonts-liberation fonts-dejavu fonts-noto-core fonts-noto-cjk \
      libxext6 libxrender1 libsm6 libfontconfig1 \
      tini \
    && rm -rf /var/lib/apt/lists/*

# ----- Python: deps -----
RUN python -m pip install --upgrade pip setuptools wheel

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# ----- Código -----
# copia com dono 65534 (nobody) para evitar chown depois
COPY --chown=65534:65534 . .

# uploads com owner/permissão corretos
RUN install -d -m 0770 -o 65534 -g 65534 /app/uploads

# usuário não-root
USER 65534:65534

# porta (documentação; Render injeta $PORT)
EXPOSE 5000

# init com tini
ENTRYPOINT ["/usr/bin/tini","--"]

# gunicorn parametrizado por env; bind em $PORT (fallback 5000)
CMD ["bash","-lc","exec gunicorn 'run:app' -b 0.0.0.0:${PORT:-5000} --workers ${WORKERS:-2} --threads ${THREADS:-8} --timeout ${TIMEOUT:-120} --graceful-timeout 30 --worker-tmp-dir /dev/shm --forwarded-allow-ips='*'"]