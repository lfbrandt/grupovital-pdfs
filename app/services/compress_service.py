import os
import subprocess
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')

def comprimir_pdf(file):
    ensure_upload_folder_exists(UPLOAD_FOLDER)

    filename = secure_filename(file.filename)
    input_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(input_path)

    output_path = os.path.join(UPLOAD_FOLDER, f"comprimido_{filename}")

    # Caminho absoluto do Ghostscript para Windows
    ghostscript_cmd = "C:\\Program Files\\gs\\gs10.05.0\\bin\\gswin64c.exe"

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

    subprocess.run(gs_cmd, check=True)

    return output_path