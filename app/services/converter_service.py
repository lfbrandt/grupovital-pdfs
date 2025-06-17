import os
import subprocess
import platform
import uuid
from flask import current_app
from werkzeug.utils import secure_filename
from PIL import Image
from ..utils.config_utils import allowed_file, ensure_upload_folder_exists

# Caminho opcional para o binário do LibreOffice.
LIBREOFFICE_BIN = os.environ.get("LIBREOFFICE_BIN")
LIBREOFFICE_TIMEOUT = int(os.environ.get("LIBREOFFICE_TIMEOUT", "60"))


def converter_doc_para_pdf(file):
    """Converte documentos suportados (DOC/DOCX/ODT) e imagens (JPG/PNG) para PDF."""
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    if not allowed_file(filename):
        raise Exception('Formato de arquivo não suportado.')

    input_path = os.path.join(upload_folder, filename)
    file.save(input_path)

    file_ext = filename.rsplit('.', 1)[1].lower()
    temp_output = os.path.splitext(input_path)[0] + '.pdf'
    unique_output = os.path.join(upload_folder, f"{uuid.uuid4().hex}.pdf")

    # Se for imagem, usa PIL para converter em PDF
    if file_ext in ['jpg', 'jpeg', 'png']:
        image = Image.open(input_path)
        rgb_image = image.convert('RGB')
        rgb_image.save(unique_output, 'PDF')
    else:
        # Para documentos, utiliza LibreOffice headless
        libreoffice_cmd = LIBREOFFICE_BIN
        if not libreoffice_cmd:
            if platform.system() == 'Windows':
                libreoffice_cmd = r"C:\Program Files\LibreOffice\program\soffice.exe"
            else:
                libreoffice_cmd = 'libreoffice'

        subprocess.run([
            libreoffice_cmd,
            '--headless',
            '--convert-to', 'pdf',
            input_path,
            '--outdir', upload_folder
        ], check=True, timeout=LIBREOFFICE_TIMEOUT)
        os.rename(temp_output, unique_output)

    os.remove(input_path)
    return unique_output


def converter_planilha_para_pdf(file):
    """Converte planilhas (CSV, XLS, XLSX) para PDF usando LibreOffice headless."""
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in ['csv', 'xls', 'xlsx']:
        raise Exception('Formato de planilha não suportado.')

    input_path = os.path.join(upload_folder, filename)
    file.save(input_path)

    # Define comando do LibreOffice conforme sistema operacional
    libreoffice_cmd = LIBREOFFICE_BIN
    if not libreoffice_cmd:
        if platform.system() == 'Windows':
            libreoffice_cmd = r"C:\Program Files\LibreOffice\program\soffice.exe"
        else:
            libreoffice_cmd = 'libreoffice'

    # Executa conversão para PDF
    subprocess.run([
        libreoffice_cmd,
        '--headless',
        '--convert-to', 'pdf',
        input_path,
        '--outdir', upload_folder
    ], check=True, timeout=LIBREOFFICE_TIMEOUT)

    temp_output = os.path.splitext(input_path)[0] + '.pdf'
    unique_output = os.path.join(upload_folder, f"{uuid.uuid4().hex}.pdf")

    # After LibreOffice run, rename to unique filename
    os.rename(temp_output, unique_output)

    os.remove(input_path)
    return unique_output
