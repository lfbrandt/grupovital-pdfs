import os
import subprocess
import platform
import uuid
import glob
import re
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications

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

def comprimir_pdf(file, rotations=None, modificacoes=None):
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    filename = validate_upload(file, {'pdf'})
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)
    apply_pdf_modifications(input_path, modificacoes)

    rotated_path = None
    if rotations:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for idx, page in enumerate(reader.pages):
            angle = rotations[idx] if idx < len(rotations) else 0
            if angle:
                try:
                    # Gira no sentido horário para bater com o preview
                    page.rotate_clockwise(angle)
                except AttributeError:
                    # Fallback para versões antigas do PyPDF2
                    page.rotate(angle)
            writer.add_page(page)

        rotated_path = os.path.join(upload_folder, f"rot_{uuid.uuid4().hex}.pdf")
        with open(rotated_path, 'wb') as f:
            writer.write(f)
        use_path = rotated_path
    else:
        use_path = input_path

    # Garante que o arquivo de saída tenha extensão .pdf
    base, _ = os.path.splitext(filename)
    output_filename = f"comprimido_{base}_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_filename)

    # Escolhe o binário do Ghostscript de acordo com o sistema
    ghostscript_cmd = GHOSTSCRIPT_BIN
    if not ghostscript_cmd:
        if platform.system() == 'Windows':
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
        use_path
    ]

    subprocess.run(gs_cmd, check=True, timeout=GHOSTSCRIPT_TIMEOUT)

    try:
        os.remove(input_path)
    except OSError:
        pass
    if rotated_path:
        try:
            os.remove(rotated_path)
        except OSError:
            pass

    return output_path