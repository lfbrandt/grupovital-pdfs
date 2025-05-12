import os
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')

def dividir_pdf(file):
    ensure_upload_folder_exists(UPLOAD_FOLDER)

    filename = secure_filename(file.filename)
    input_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(input_path)

    reader = PdfReader(input_path)
    output_files = []

    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)

        output_path = os.path.join(UPLOAD_FOLDER, f"pagina_{i + 1}.pdf")
        with open(output_path, 'wb') as f_out:
            writer.write(f_out)

        output_files.append(output_path)

    return output_files