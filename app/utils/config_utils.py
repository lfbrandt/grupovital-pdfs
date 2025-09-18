# app/utils/config_utils.py
import os
import time
import mimetypes
from pathlib import Path
from werkzeug.utils import secure_filename

from .security import (
    sanitize_filename,
    detect_mime_from_buffer,  # usa python-magic / python-magic-bin no Windows (com fallback)
    is_allowed_mime,
)

# Catálogo global (referência). Cada rota ainda informa seu subconjunto permitido.
ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'html',
    'xls', 'xlsx', 'ods',
    'ppt', 'pptx', 'odp',
    'jpg', 'jpeg', 'png', 'bmp', 'tiff',
}

def allowed_file(filename: str) -> bool:
    """True se o arquivo possui extensão permitida no catálogo global."""
    return (
        isinstance(filename, str)
        and '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )

def ensure_upload_folder_exists(upload_folder: str):
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

def _filename_from_obj(file) -> str:
    """Extrai um nome útil a partir de FileStorage/str/path/file-like."""
    if hasattr(file, 'filename') and file.filename:
        return file.filename
    if hasattr(file, 'name') and isinstance(file.name, str) and file.name:
        return os.path.basename(file.name)
    if isinstance(file, (str, os.PathLike)):
        return os.path.basename(str(file))
    return ''

def _read_head(file, nbytes=8192) -> bytes:
    """Lê n bytes do stream sem consumi-lo (faz seek de volta quando possível)."""
    stream = getattr(file, 'stream', None)
    if not stream or not hasattr(stream, 'read'):
        return b''
    pos = None
    try:
        pos = stream.tell()
    except Exception:
        pos = None
    try:
        head = stream.read(nbytes) or b''
    finally:
        try:
            if pos is not None:
                stream.seek(pos, os.SEEK_SET)
        except Exception:
            pass
    return head

def _infer_ext_by_mime(file) -> str:
    """Tenta inferir extensão a partir do MIME real (buffer) e/ou do MIME informado pelo navegador."""
    head = _read_head(file)
    real_mime = (detect_mime_from_buffer(head) or '').lower()
    browser_mime = (getattr(file, 'mimetype', '') or getattr(file, 'content_type', '') or '').lower()
    mime = real_mime or browser_mime

    # Mapeamento mínimo (expanda se necessário)
    if mime.startswith('application/pdf'):
        return 'pdf'
    if mime in ('image/jpeg', 'image/jpg'):
        return 'jpg'
    if mime == 'image/png':
        return 'png'
    if mime in ('image/bmp', 'image/x-ms-bmp'):
        return 'bmp'
    if mime in ('image/tiff', 'image/x-tiff'):
        return 'tiff'
    if mime == 'text/plain':
        return 'txt'
    if mime in ('text/html', 'application/xhtml+xml'):
        return 'html'
    if mime == 'application/msword':
        return 'doc'
    if mime == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        return 'docx'
    if mime == 'application/vnd.ms-excel':
        return 'xls'
    if mime == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        return 'xlsx'
    if mime == 'application/vnd.ms-powerpoint':
        return 'ppt'
    if mime == 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        return 'pptx'
    if mime == 'application/vnd.oasis.opendocument.text':
        return 'odt'
    if mime == 'application/vnd.oasis.opendocument.spreadsheet':
        return 'ods'
    if mime == 'application/vnd.oasis.opendocument.presentation':
        return 'odp'
    return ''

def validate_upload(file, allowed_extensions):
    """
    Valida nome, extensão e MIME real antes do processamento.

    Regras:
      - NÃO assume que existe ponto no nome.
      - Se a sanitização remover a extensão, usa fallback do nome original (raw).
      - Se ainda faltar, tenta inferir por MIME real (buffer) e/ou browser MIME.
      - Se 'allowed_extensions' for informado, faz cumprir.
      - Checa MIME real contra a allowlist (is_allowed_mime).

    Retorna: nome de arquivo sanitizado (string), com extensão coerente quando possível.
    Levanta: ValueError (p/ 400) em erros previstos; Exception em erros inesperados.
    """
    # 1) Nome original e extensão "bruta" (antes da sanitização)
    raw_name = _filename_from_obj(file) or ''
    raw_ext = raw_name.rsplit('.', 1)[1].lower() if '.' in raw_name else ''

    # 2) Sanitização dupla e extração de extensão pós-sanitização
    fname = secure_filename(sanitize_filename(raw_name or ''))
    ext_sanitized = Path(fname).suffix.lower().lstrip('.') if fname else ''

    # 3) Inferência por MIME
    ext_inferred = _infer_ext_by_mime(file)

    # 4) Escolha da extensão final (primeira disponível)
    ext = ext_sanitized or ext_inferred or raw_ext

    # 5) MIME real (sem gravar em disco) → bloqueia spoof óbvio
    head = _read_head(file)
    real_mime = (detect_mime_from_buffer(head) or '').lower()
    if real_mime and real_mime != 'application/octet-stream':
        if not is_allowed_mime(real_mime):
            raise ValueError(f"Tipo MIME não permitido: {real_mime}")

    # 6) Checagem leve com cabeçalho do browser (apenas inconsistências graves)
    guessed = (mimetypes.guess_type(fname or (f'_.{ext}' if ext else None))[0]
               if (fname or ext) else None)
    browser_mime = (getattr(file, "mimetype", None) or '').lower()
    if browser_mime and guessed and browser_mime != 'application/octet-stream':
        if browser_mime != guessed and (real_mime and real_mime != browser_mime):
            raise ValueError("Conflito de MIME detectado.")

    # 7) Cumprimento das extensões permitidas na rota/serviço
    if allowed_extensions:
        allowed = {str(e).lower().lstrip('.') for e in allowed_extensions}
        if not ext:
            ext = ext_inferred or raw_ext
        if not ext:
            raise ValueError("Arquivo sem extensão reconhecível.")
        if ext not in allowed:
            raise ValueError(f"Extensão de arquivo não suportada: .{ext}")

    # 8) Garante um nome retornável e coerente com a extensão escolhida
    if not fname:
        fname = f'upload.{ext or "bin"}'
    else:
        if ext and not fname.lower().endswith(f'.{ext}'):
            fname = f"{Path(fname).stem}.{ext}"

    return fname

def clean_old_uploads(upload_folder: str, max_age_hours: int):
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