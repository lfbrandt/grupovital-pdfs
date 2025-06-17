import os
import uuid
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

def dividir_pdf(file):
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    if not filename.lower().endswith('.pdf'):
        raise Exception('Apenas arquivos PDF são permitidos.')
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)

    reader = PdfReader(input_path)
    output_files = []

    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)

        output_filename = f"pagina_{i + 1}_{uuid.uuid4().hex}.pdf"
        output_path = os.path.join(upload_folder, output_filename)
        with open(output_path, 'wb') as f_out:
            writer.write(f_out)

        output_files.append(output_path)

    os.remove(input_path)
    return output_files
