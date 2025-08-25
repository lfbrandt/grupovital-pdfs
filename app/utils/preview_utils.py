import os
import re
import hashlib
from PIL import Image
import pypdfium2 as pdfium
from flask import current_app

# ===== Config =====
THUMB_MAX_WIDTH = 512        # largura máxima da miniatura (mantém proporção)
THUMBS_SUBDIR   = "_thumbs"  # subpasta de cache dentro de UPLOAD_FOLDER
SAFE_NAME_RE    = re.compile(r"^[a-f0-9]{16,64}$")  # nomes seguros (hash hex)

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _thumbs_dir() -> str:
    base = current_app.config.get("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
    thumbs = os.path.join(base, THUMBS_SUBDIR)
    os.makedirs(thumbs, exist_ok=True)
    return thumbs

def _render_first_page_to_png(src_pdf_path: str, out_png_path: str, dpi: int = 144) -> None:
    """
    Renderiza SOMENTE a primeira página do PDF em PNG (com pypdfium2),
    redimensionando para THUMB_MAX_WIDTH (mantendo proporção).
    """
    pdf = pdfium.PdfDocument(src_pdf_path)
    if len(pdf) < 1:
        raise ValueError("PDF sem páginas.")

    page = pdf.get_page(0)
    bitmap = page.render(scale=(dpi / 72.0))
    pil = bitmap.to_pil()

    if pil.width > THUMB_MAX_WIDTH:
        new_h = int(pil.height * (THUMB_MAX_WIDTH / pil.width))
        pil = pil.resize((THUMB_MAX_WIDTH, new_h), Image.LANCZOS)

    pil.save(out_png_path, format="PNG", optimize=True)

    page.close()
    pdf.close()

def preview_pdf(abs_pdf_path: str) -> dict:
    """
    Gera (ou reutiliza do cache) a miniatura PNG da 1ª página do PDF.

    Retorna:
    {
      "thumb_id": "<hash40>",
      "thumb_path": "<path-absoluto>",
      "filename": "<thumb_id>.png"
    }
    """
    if not os.path.exists(abs_pdf_path):
        raise FileNotFoundError("Arquivo PDF não encontrado.")

    full_hash = _sha256_file(abs_pdf_path)
    thumb_id = full_hash[:40]  # id curto e seguro

    if not SAFE_NAME_RE.match(thumb_id):
        raise ValueError("Identificador de miniatura inválido.")

    thumbs_dir = _thumbs_dir()
    thumb_filename = f"{thumb_id}.png"
    thumb_path = os.path.join(thumbs_dir, thumb_filename)

    if not os.path.exists(thumb_path):
        _render_first_page_to_png(abs_pdf_path, thumb_path)

    return {
        "thumb_id": thumb_id,
        "thumb_path": thumb_path,
        "filename": thumb_filename,
    }