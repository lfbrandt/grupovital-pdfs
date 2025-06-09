import os
from PyPDF2 import PdfMerger
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')

def juntar_pdfs(files):
    ensure_upload_folder_exists(UPLOAD_FOLDER)

    merger = PdfMerger()
    filenames = []

    for file in files:
        filename = secure_filename(file.filename)
        if not filename.lower().endswith('.pdf'):
            raise Exception('Apenas arquivos PDF são permitidos.')
        path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(path)
        filenames.append(path)
        merger.append(path)

    output_path = os.path.join(UPLOAD_FOLDER, 'merged_output.pdf')
    merger.write(output_path)
    merger.close()

    for f in filenames:
        try:
            os.remove(f)
        except OSError:
            pass

    return output_path
