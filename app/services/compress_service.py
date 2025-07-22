import os
import subprocess
import platform
import uuid
import glob
import re
import base64
import tempfile
from io import BytesIO
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from pdf2image import convert_from_bytes
import fitz
from ..utils.config_utils import ensure_upload_folder_exists


def gerar_previews(file):
    """Gera miniaturas PNG para cada página do PDF e retorna seus caminhos."""
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    temp_name = f"prev_{uuid.uuid4().hex}.pdf"
    temp_path = os.path.join(upload_folder, temp_name)
    file.save(temp_path)

    doc = fitz.open(temp_path)
    images = []
    for pg in doc:
        pix = pg.get_pixmap(matrix=fitz.Matrix(0.3, 0.3))
        img_name = f"thumb_{uuid.uuid4().hex}.png"
        img_path = os.path.join(upload_folder, img_name)
        pix.save(img_path)
        images.append(img_name)
    doc.close()
    os.remove(temp_path)
    return images


# Gera data-URIs PNG de cada página do PDF para preview no front-end
def preview_pdf(file, dpi=50):
    data = file.read()
    images = convert_from_bytes(data, dpi=dpi)
    uris = []
    for img in images:
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        uris.append(f"data:image/png;base64,{b64}")
    return uris


# Caminho opcional para o binário do Ghostscript.
GHOSTSCRIPT_BIN = os.environ.get("GHOSTSCRIPT_BIN")
GHOSTSCRIPT_TIMEOUT = int(os.environ.get("GHOSTSCRIPT_TIMEOUT", "60"))


def _locate_windows_ghostscript():
    """Busca o executável do Ghostscript em pastas comuns do Windows."""
    patterns = [
        r"C:\\Program Files\\gs\\*\\bin\\gswin64c.exe",
        r"C:\\Program Files (x86)\\gs\\*\\bin\\gswin64c.exe",
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    if not candidates:
        return None

    def version_key(path):
        match = re.search(r"gs(\d+(?:\.\d+)*)", path)
        if match:
            return [int(p) for p in match.group(1).split(".")]
        return [0]

    return max(candidates, key=version_key)


def comprimir_pdf(
    file,
    *,
    level="ebook",
    mods: dict | None = None,
    rotations: dict | None = None,
    modificacoes: dict | None = None,
):
    """Comprime um PDF aplicando rotações e remoções de páginas.

    Parâmetros legados ``rotations`` e ``modificacoes`` são mesclados ao ``mods``
    mais novo para manter compatibilidade.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    temp_in = tempfile.NamedTemporaryFile(
        dir=upload_folder, suffix=".pdf", delete=False
    ).name
    file.save(temp_in)

    # Aplicar modificações (crop/rotate global) caso existam
    if modificacoes or (mods and mods.get("modificacoes")):
        from ..utils.pdf_utils import apply_pdf_modifications

        apply_pdf_modifications(temp_in, modificacoes or mods.get("modificacoes"))

    # Rotação e remoção de páginas específicas
    intermediate = temp_in
    mods = mods or {}
    rotations = rotations or mods.get("rotations", {})
    removed = set(mods.get("removed", []))

    if rotations or removed:
        reader = PdfReader(temp_in)
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i in removed:
                continue
            angle = rotations.get(str(i)) or rotations.get(i) or 0
            if angle:
                try:
                    page.rotate_clockwise(angle)
                except Exception:
                    page.rotate(angle)
            writer.add_page(page)
        intermediate = tempfile.NamedTemporaryFile(
            dir=upload_folder, suffix=".pdf", delete=False
        ).name
        with open(intermediate, "wb") as f:
            writer.write(f)

    use_path = intermediate

    output_path = tempfile.NamedTemporaryFile(
        dir=upload_folder, suffix=".pdf", delete=False
    ).name

    # Escolhe o binário do Ghostscript de acordo com o sistema
    ghostscript_cmd = GHOSTSCRIPT_BIN
    if not ghostscript_cmd:
        if platform.system() == "Windows":
            ghostscript_cmd = _locate_windows_ghostscript()
        if not ghostscript_cmd:
            ghostscript_cmd = "gs"

    gs_cmd = [
        ghostscript_cmd,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS=/{level}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_path}",
        use_path,
    ]

    subprocess.run(gs_cmd, check=True, timeout=GHOSTSCRIPT_TIMEOUT)
    os.remove(temp_in)
    if intermediate != temp_in:
        try:
            os.remove(intermediate)
        except OSError:
            pass
    return output_path
