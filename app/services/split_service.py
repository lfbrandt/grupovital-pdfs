import os
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

def dividir_pdf(file):
    upload_folder = os.path.join(os.getcwd(), 'uploads')
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    if not filename.lower().endswith('.pdf'):
        raise Exception('Apenas arquivos PDF são permitidos.')
    input_path = os.path.join(upload_folder, filename)
    file.save(input_path)

    reader = PdfReader(input_path)
    output_files = []

    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)

        output_path = os.path.join(upload_folder, f"pagina_{i + 1}.pdf")
        with open(output_path, 'wb') as f_out:
            writer.write(f_out)

        output_files.append(output_path)

    os.remove(input_path)
    return output_files
