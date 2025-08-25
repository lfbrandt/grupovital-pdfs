import os
import mimetypes
import time
from werkzeug.utils import secure_filename

from .security import (
    sanitize_filename,
    detect_mime_from_buffer,
    is_allowed_mime,
)

# Extensões de arquivo aceitas (controle por extensão)
ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'html',
    'xls', 'xlsx', 'ods',
    'ppt', 'pptx', 'odp',
    'jpg', 'jpeg', 'png', 'bmp', 'tiff',
}

def allowed_file(filename):
    """Retorna True se o arquivo possuir uma extensão permitida."""
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )

def ensure_upload_folder_exists(upload_folder):
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

def validate_upload(file, allowed_extensions):
    """
    Valida extensão e MIME real antes do processamento.
    - Extensão precisa estar em 'allowed_extensions'
    - MIME real (python-magic via buffer) precisa estar na lista branca permitida
    - Evita vazar dados sensíveis: não grava nada em disco aqui
    Retorna: nome de arquivo sanitizado (string)
    """
    raw_name = file.filename or ""
    if "." not in raw_name:
        raise Exception("Extensão de arquivo inválida.")

    # Sanitização de nome (dupla: our + werkzeug)
    fname = sanitize_filename(raw_name)
    fname = secure_filename(fname)

    ext = fname.rsplit('.', 1)[1].lower()
    if ext not in allowed_extensions:
        raise Exception("Extensão de arquivo não suportada.")

    # MIME real por buffer (sem gravar em disco)
    # lê poucos bytes e reseta o cursor
    try:
        pos = file.stream.tell()
    except Exception:
        pos = None
    head = file.stream.read(8192)  # 8KB é suficiente para detecção
    try:
        if pos is not None:
            file.stream.seek(pos)
    except Exception:
        pass

    real_mime = detect_mime_from_buffer(head)  # fallback já tratado no util
    if real_mime and real_mime != "application/octet-stream":
        if not is_allowed_mime(real_mime):
            raise Exception(f"Tipo MIME não permitido: {real_mime}")

    # Validação leve com cabeçalho enviado pelo browser (não confiável, mas ajuda)
    guessed = mimetypes.guess_type(fname)[0]
    browser_mime = getattr(file, "mimetype", None)
    if browser_mime and guessed and browser_mime != "application/octet-stream":
        # tolera quando browser reporta algo genérico; não sobrepõe o 'real_mime'
        if browser_mime != guessed and (real_mime and real_mime != browser_mime):
            # conflito entre cabeçalhos e detecção real
            raise Exception("Conflito de MIME detectado.")

    return fname

def clean_old_uploads(upload_folder, max_age_hours):
    """Remove arquivos mais antigos que 'max_age_hours' horas."""
    now = time.time()
    cutoff = now - max_age_hours * 3600
    if not os.path.isdir(upload_folder):
        return
    for name in os.listdir(upload_folder):
        path = os.path.join(upload_folder, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass