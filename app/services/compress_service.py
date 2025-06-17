import os
import subprocess
import platform
import uuid
from flask import current_app
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

# Caminho opcional para o binário do Ghostscript.
GHOSTSCRIPT_BIN = os.environ.get("GHOSTSCRIPT_BIN")

def comprimir_pdf(file):
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    if not filename.lower().endswith('.pdf'):
        raise Exception('Apenas arquivos PDF s\u00e3o permitidos.')
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)

    # Garante que o arquivo de saída tenha extensão .pdf
    base, _ = os.path.splitext(filename)
    output_filename = f"comprimido_{base}_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_filename)

    # Escolhe o binário do Ghostscript de acordo com o sistema
    ghostscript_cmd = GHOSTSCRIPT_BIN
    if not ghostscript_cmd:
        if platform.system() == 'Windows':
            ghostscript_cmd = r"C:\Program Files\gs\gs10.05.0\bin\gswin64c.exe"
        else:
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
        input_path
    ]

    subprocess.run(gs_cmd, check=True, timeout=60)
    os.remove(input_path)
    return output_path
