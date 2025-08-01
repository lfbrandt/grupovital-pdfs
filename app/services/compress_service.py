import os
import subprocess
import platform
import uuid
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications

# Configuração do Ghostscript
GHOSTSCRIPT_TIMEOUT = int(os.environ.get("GHOSTSCRIPT_TIMEOUT", "60"))


def _locate_windows_ghostscript():
    """Localiza o executável do Ghostscript em sistemas Windows."""
    from glob import glob
    import re

    patterns = [
        r"C:\\Program Files\\gs\\*\\bin\\gswin64c.exe",
        r"C:\\Program Files (x86)\\gs\\*\\bin\\gswin64c.exe",
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob(pat))
    if not candidates:
        return None

    def version_key(path):
        m = re.search(r"gs(\d+(?:\.\d+)*)", path)
        return [int(x) for x in m.group(1).split('.')] if m else [0]

    return max(candidates, key=version_key)


def _get_ghostscript_cmd():
    gs = os.environ.get("GHOSTSCRIPT_BIN")
    if not gs and platform.system() == 'Windows':
        gs = _locate_windows_ghostscript()
    return gs or 'gs'


def _run_ghostscript(input_pdf: str, output_pdf: str):
    """Executa o Ghostscript para comprimir o PDF."""
    gs_cmd = _get_ghostscript_cmd()
    cmd = [
        gs_cmd,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dPDFSETTINGS=/ebook",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_pdf}",
        input_pdf,
    ]
    subprocess.run(cmd, check=True, timeout=GHOSTSCRIPT_TIMEOUT)


def comprimir_pdf(file, rotations=None, modificacoes=None):
    """
    Comprime um arquivo PDF, aplicando rotações e modificações antes da compressão.
    Retorna o caminho do PDF comprimido.
    """
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    # Validação e salvamento do arquivo original
    filename = validate_upload(file, {'pdf'})
    basename = os.path.splitext(filename)[0]
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)

    # Aplicar modificações genéricas (crop, rotação única)
    if modificacoes:
        apply_pdf_modifications(input_path, modificacoes)

    # Aplicar rotações por página, se fornecidas
    temp_source = input_path
    if rotations:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            angle = rotations[i] if i < len(rotations) else 0
            if angle:
                # Usa rotate() (PyPDF2 >= 3.0.0)
                page.rotate(angle)
            writer.add_page(page)

        rotated_file = f"rot_{uuid.uuid4().hex}.pdf"
        rotated_path = os.path.join(upload_folder, rotated_file)
        with open(rotated_path, 'wb') as out_f:
            writer.write(out_f)

        temp_source = rotated_path
        # Remover arquivo intermediário original
        try:
            os.remove(input_path)
        except OSError:
            pass

    # Preparar saída comprimida
    output_name = f"comprimido_{basename}_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_name)

    # Executar compressão via Ghostscript
    _run_ghostscript(temp_source, output_path)

    # Limpar arquivo rotacionado intermediário
    try:
        if rotations:
            os.remove(temp_source)
    except OSError:
        pass

    return output_path