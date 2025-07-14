import os
import subprocess
import platform
import uuid
import glob
import re
from flask import current_app
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists
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


def comprimir_pdf(file, modificacoes=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        raise Exception("Apenas arquivos PDF s\u00e3o permitidos.")
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)
    apply_pdf_modifications(input_path, modificacoes)

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
        input_path,
    ]

    subprocess.run(gs_cmd, check=True, timeout=GHOSTSCRIPT_TIMEOUT)
    os.remove(input_path)
    return output_path
