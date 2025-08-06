import os
import subprocess
import platform
import tempfile
from flask import current_app
from PIL import Image
from ..utils.config_utils import (
    ALLOWED_EXTENSIONS,
    ensure_upload_folder_exists,
    validate_upload,
)
from ..utils.pdf_utils import apply_pdf_modifications, apply_image_modifications

LIBREOFFICE_BIN = os.environ.get("LIBREOFFICE_BIN")
LIBREOFFICE_TIMEOUT = int(os.environ.get("LIBREOFFICE_TIMEOUT", "120"))

def _get_libreoffice_cmd():
    if LIBREOFFICE_BIN:
        return LIBREOFFICE_BIN
    return r"C:\Program Files\LibreOffice\program\soffice.exe" if platform.system() == "Windows" else "libreoffice"

def converter_doc_para_pdf(file, modificacoes=None):
    """Converte DOC/DOCX/ODT/TXT/RTF/HTML e imagens (JPG/PNG/etc) para PDF. Suporta modificações."""
    file_ext = file.filename.rsplit('.', 1)[-1].lower()

    if file_ext == "pdf":
        raise ValueError("PDF já é um arquivo final. Não pode ser convertido.")

    input_temp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}")
    file.save(input_temp.name)

    output_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")

    if file_ext in ['jpg', 'jpeg', 'png', 'bmp', 'tiff']:
        image = Image.open(input_temp.name)
        image = apply_image_modifications(image, modificacoes)
        rgb = image.convert("RGB")
        rgb.save(output_temp.name, "PDF")
    else:
        subprocess.run([
            _get_libreoffice_cmd(),
            "--headless",
            "--convert-to", "pdf",
            input_temp.name,
            "--outdir", os.path.dirname(output_temp.name)
        ], check=True, timeout=LIBREOFFICE_TIMEOUT)

        temp_output_generated = os.path.splitext(input_temp.name)[0] + ".pdf"
        os.rename(temp_output_generated, output_temp.name)
        apply_pdf_modifications(output_temp.name, modificacoes)

    os.remove(input_temp.name)
    return output_temp.name

def converter_planilha_para_pdf(file, modificacoes=None):
    """Converte CSV, XLS, XLSX, ODS para PDF. Usa LibreOffice headless."""
    file_ext = file.filename.rsplit('.', 1)[-1].lower()

    input_temp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}")
    file.save(input_temp.name)

    output_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")

    subprocess.run([
        _get_libreoffice_cmd(),
        "--headless",
        "--convert-to", "pdf",
        input_temp.name,
        "--outdir", os.path.dirname(output_temp.name)
    ], check=True, timeout=LIBREOFFICE_TIMEOUT)

    temp_output_generated = os.path.splitext(input_temp.name)[0] + ".pdf"
    os.rename(temp_output_generated, output_temp.name)
    apply_pdf_modifications(output_temp.name, modificacoes)

    os.remove(input_temp.name)
    return output_temp.name