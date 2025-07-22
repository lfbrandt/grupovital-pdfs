import os
import subprocess
import platform
import uuid
import glob
import re
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
import fitz
from .pdf_common import process_pdf_action
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


def comprimir_pdf(file, *, rotations=None, modificacoes=None, mods=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]

    input_path = process_pdf_action([file], modificacoes=modificacoes)[0]
    filename = os.path.basename(input_path)

    intermediate_path = input_path

    if mods:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        removed = set(mods.get("removed", []))
        rotations_map = mods.get("rotations", {})
        for i, page in enumerate(reader.pages):
            if i in removed:
                continue
            angle = rotations_map.get(str(i)) or rotations_map.get(i) or 0
            if angle:
                try:
                    page.rotate_clockwise(angle)
                except Exception:
                    page.rotate(angle)
            writer.add_page(page)
        intermediate_path = os.path.join(upload_folder, f"mods_{uuid.uuid4().hex}.pdf")
        with open(intermediate_path, "wb") as f:
            writer.write(f)

    elif rotations:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for idx, page in enumerate(reader.pages):
            angle = rotations[idx] if idx < len(rotations) else 0
            if angle:
                try:
                    page.rotate_clockwise(angle)
                except Exception:
                    page.rotate(angle)
            writer.add_page(page)

        intermediate_path = os.path.join(upload_folder, f"rot_{uuid.uuid4().hex}.pdf")
        with open(intermediate_path, "wb") as f:
            writer.write(f)

    use_path = intermediate_path

    # Garante que o arquivo de saída tenha extensão .pdf
    base, _ = os.path.splitext(filename)
    output_filename = f"comprimido_{base}_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_filename)

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
        "-dPDFSETTINGS=/ebook",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_path}",
        use_path,
    ]

    subprocess.run(gs_cmd, check=True, timeout=GHOSTSCRIPT_TIMEOUT)
    os.remove(input_path)
    if intermediate_path != input_path:
        try:
            os.remove(intermediate_path)
        except OSError:
            pass
    return output_path
