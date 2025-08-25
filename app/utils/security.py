import os
import re
import mimetypes

try:
    import magic  # python-magic
except Exception:  # pragma: no cover
    magic = None

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]")

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
    return default

def is_allowed_mime(mime: str) -> bool:
    return mime in ALLOWED_MIMES