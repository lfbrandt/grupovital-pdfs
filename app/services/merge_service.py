import os
from PyPDF2 import PdfMerger
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

def juntar_pdfs(files):
    upload_folder = os.path.join(os.getcwd(), 'uploads')
    ensure_upload_folder_exists(upload_folder)

    merger = PdfMerger()
    filenames = []

    for file in files:
        filename = secure_filename(file.filename)
        if not filename.lower().endswith('.pdf'):
            raise Exception('Apenas arquivos PDF são permitidos.')
        path = os.path.join(upload_folder, filename)
        file.save(path)
        filenames.append(path)
        merger.append(path)

    output_path = os.path.join(upload_folder, 'merged_output.pdf')
    merger.write(output_path)
    merger.close()

    for f in filenames:
        try:
            os.remove(f)
        except OSError:
            pass

    return output_path
