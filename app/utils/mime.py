# -*- coding: utf-8 -*-
"""Helpers de MIME + sanitização de nome de arquivo."""
from __future__ import annotations
import os
import re
import mimetypes

try:
    import magic  # python-magic
except Exception:  # pragma: no cover
    magic = None

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]")
PDF_SIG = b"%PDF-"

# Lista branca de MIME types aceitos
ALLOWED_MIMES = {
    # PDF
    "application/pdf",
    # Documentos
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/rtf",
    "text/plain",
    "text/html",
    "application/vnd.oasis.opendocument.text",
    # Planilhas
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
    # Apresentações
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.presentation",
    # Imagens
    "image/jpeg", "image/png", "image/bmp", "image/tiff",
}

def sanitize_filename(name: str) -> str:
    base = os.path.basename(name or "")
    cleaned = SAFE_FILENAME_RE.sub("_", base)
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "file"
    return cleaned[:128]

def detect_mime(path: str, default: str = "application/octet-stream") -> str:
    """Detecta MIME usando python-magic por arquivo; cai para mimetypes por extensão."""
    if magic:
        try:
            m = magic.Magic(mime=True)
            mt = m.from_file(path)
            if mt:
                return mt
        except Exception:
            pass
    guess, _ = mimetypes.guess_type(path)
    return guess or default

def detect_mime_from_buffer(buf: bytes, default: str = "application/octet-stream") -> str:
    """Detecta MIME a partir de bytes (para uploads em memória)."""
    if magic:
        try:
            m = magic.Magic(mime=True)
            mt = m.from_buffer(buf)
            if mt:
                return mt
        except Exception:
            pass
    # fallback mínimo
    if buf[:5] == PDF_SIG:
        return "application/pdf"
    return default

def detect_mime_or_ext(upload_or_path, default: str = "application/octet-stream") -> str:
    """
    Detecta MIME real. Usa python-magic se disponível; fallback: assinatura PDF.
    Aceita arquivo de upload (obj com .stream) ou caminho str.
    """
    if magic:
        try:
            if hasattr(upload_or_path, "stream"):
                pos = upload_or_path.stream.tell()
                head = upload_or_path.stream.read(8192)
                upload_or_path.stream.seek(pos)
                return magic.from_buffer(head, mime=True)
            else:
                return magic.from_file(str(upload_or_path), mime=True)
        except Exception:
            pass
    # fallback básico por assinatura
    try:
        if hasattr(upload_or_path, "stream"):
            pos = upload_or_path.stream.tell()
            head = upload_or_path.stream.read(5)
            upload_or_path.stream.seek(pos)
            if head == PDF_SIG:
                return "application/pdf"
        else:
            with open(upload_or_path, "rb") as fh:
                if fh.read(5) == PDF_SIG:
                    return "application/pdf"
    except Exception:
        pass
    return default

def is_allowed_mime(mime: str) -> bool:
    return mime in ALLOWED_MIMES