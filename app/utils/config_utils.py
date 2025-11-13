# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
import mimetypes
import logging
from pathlib import Path
from werkzeug.utils import secure_filename

from .security import (
    sanitize_filename,
    detect_mime_from_buffer,  # python-magic / python-magic-bin no Windows
    is_allowed_mime,
)

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {
    'pdf',
    'doc', 'docx', 'odt', 'rtf', 'txt', 'html',
    'xls', 'xlsx', 'xlsm', 'ods',
    'ppt', 'pptx', 'odp',
    'jpg', 'jpeg', 'png', 'bmp', 'tiff',
    'csv',
}


def allowed_file(filename: str) -> bool:
    return (
        isinstance(filename, str)
        and '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def ensure_upload_folder_exists(upload_folder: str):
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder, exist_ok=True)
        try:
            os.chmod(upload_folder, 0o700)
        except Exception:
            pass


def _filename_from_obj(file) -> str:
    if hasattr(file, 'filename') and file.filename:
        return file.filename
    if hasattr(file, 'name') and isinstance(file.name, str) and file.name:
        return os.path.basename(file.name)
    if isinstance(file, (str, os.PathLike)):
        return os.path.basename(str(file))
    return ''


def _read_head(file, nbytes=8192) -> bytes:
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
    head = _read_head(file)
    real_mime = (detect_mime_from_buffer(head) or '').lower()
    browser_mime = (getattr(file, 'mimetype', '') or getattr(file, 'content_type', '') or '').lower()
    mime = real_mime or browser_mime

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
    if mime == 'application/vnd.ms-excel.sheet.macroenabled.12':
        return 'xlsm'
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
    if mime in ('text/csv', 'application/csv'):
        return 'csv'
    return ''


def validate_upload(file, allowed_extensions):
    """
    Valida nome, extensão e MIME real antes do processamento.
    Retorna nome sanitizado com extensão coerente, fazendo fallback
    por MIME real e pela extensão original quando necessário.
    """
    # Nome original e extensão "crua"
    raw_name = _filename_from_obj(file) or ''
    raw_ext = raw_name.rsplit('.', 1)[1].lower() if '.' in raw_name else ''

    # Nome sanitizado (sem caracteres perigosos) e extensão "do sanitizado"
    fname = secure_filename(sanitize_filename(raw_name or ''))
    ext_sanitized = Path(fname).suffix.lower().lstrip('.') if fname else ''

    # MIME real e inferência de extensão por MIME
    head = _read_head(file)
    real_mime = (detect_mime_from_buffer(head) or '').lower()
    ext_inferred = _infer_ext_by_mime(file)

    # Checagem de MIME real contra a allowlist de MIME (se configurada)
    if real_mime and real_mime != 'application/octet-stream':
        if not is_allowed_mime(real_mime):
            raise ValueError(f"Tipo MIME não permitido: {real_mime}")

    # Candidatas de extensão a partir dos sufixos (raw e sanitizado)
    raw_suffixes = [s.lstrip('.').lower() for s in Path(raw_name).suffixes]
    san_suffixes = [s.lstrip('.').lower() for s in Path(fname).suffixes]

    # Se o nome original terminar com ".pdf", privilegiaremos pdf
    ends_with_pdf = raw_name.lower().endswith('.pdf') or fname.lower().endswith('.pdf')

    # Seleção da extensão final respeitando a allowlist
    ext = ''
    if allowed_extensions:
        allowed = {str(e).lower().lstrip('.') for e in allowed_extensions}

        # 1) Se terminar com .pdf e pdf for permitido, use pdf
        if ends_with_pdf and 'pdf' in allowed:
            ext = 'pdf'

        # 2) Procura, da direita pra esquerda, algum sufixo permitido no NOME SANITIZADO
        if not ext and san_suffixes:
            for sfx in reversed(san_suffixes):
                if sfx in allowed:
                    ext = sfx
                    break

        # 3) Procura em sufixos do NOME ORIGINAL (caso sanitização tenha removido o .pdf)
        if not ext and raw_suffixes:
            for sfx in reversed(raw_suffixes):
                if sfx in allowed:
                    ext = sfx
                    break

        # 4) Usa a extensão inferida por MIME se for permitida
        if not ext and ext_inferred and ext_inferred in allowed:
            ext = ext_inferred

        # 5) Usa a extensão "crua" se for permitida
        if not ext and raw_ext and raw_ext in allowed:
            ext = raw_ext

        # 6) Último recurso: se o MIME real for PDF, força pdf
        if not ext and real_mime == 'application/pdf' and 'pdf' in allowed:
            ext = 'pdf'

        if not ext:
            # Monta erro mais claro
            bad_ext = (Path(fname).suffix or Path(raw_name).suffix or '').lstrip('.')
            raise ValueError(f"Extensão de arquivo não suportada: .{bad_ext or 'desconhecida'}")
    else:
        # Sem allowlist explícita de extensões, tenta algo razoável
        ext = ext_sanitized or ext_inferred or raw_ext

    # Garante um nome final com a extensão escolhida
    if not fname:
        fname = f'upload.{ext or "bin"}'
    else:
        # Se o nome sanitizado perdeu a extensão ou ficou errada, reanexa a certa
        if ext and not fname.lower().endswith(f'.{ext}'):
            fname = f"{Path(fname).stem}.{ext}"

    # Log informativo quando houver divergência de MIME do navegador x guess x real
    try:
        browser_mime = (getattr(file, 'mimetype', '') or getattr(file, 'content_type', '') or '').lower()
        guessed = (mimetypes.guess_type(fname or (f'_.{ext}' if ext else None))[0]
                   if (fname or ext) else None)
        if browser_mime and guessed and browser_mime != 'application/octet-stream':
            if browser_mime != guessed and (real_mime and real_mime != browser_mime):
                log.debug(
                    "MIME do navegador (%s) difere do guess (%s) e do real (%s) para '%s'",
                    browser_mime, guessed, real_mime or '-', raw_name
                )
    except Exception:
        pass

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