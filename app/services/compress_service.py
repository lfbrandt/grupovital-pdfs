import os
import subprocess
import platform
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')

def comprimir_pdf(file):
    ensure_upload_folder_exists(UPLOAD_FOLDER)

    filename = secure_filename(file.filename)
    input_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(input_path)

    # Garante que o arquivo de saída tenha extensão .pdf
    base, _ = os.path.splitext(filename)
    output_path = os.path.join(UPLOAD_FOLDER, f"comprimido_{base}.pdf")

    # Escolhe o binário do Ghostscript de acordo com o sistema
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

    subprocess.run(gs_cmd, check=True)
    return output_path