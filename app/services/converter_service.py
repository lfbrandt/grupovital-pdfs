import os
import subprocess
import platform
from werkzeug.utils import secure_filename
from PIL import Image
from ..utils.config_utils import allowed_file, ensure_upload_folder_exists

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')


def converter_doc_para_pdf(file):
    ensure_upload_folder_exists(UPLOAD_FOLDER)

    filename = secure_filename(file.filename)
    input_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(input_path)

    if not allowed_file(filename):
        raise Exception('Formato de arquivo não suportado.')

    file_ext = filename.rsplit('.', 1)[1].lower()
    output_path = os.path.splitext(input_path)[0] + '.pdf'

    if file_ext in ['jpg', 'jpeg', 'png']:
        image = Image.open(input_path)
        rgb_image = image.convert('RGB')
        rgb_image.save(output_path, 'PDF')
    else:
        if platform.system() == 'Windows':
            libreoffice_cmd = 'C:\\Program Files\\LibreOffice\\program\\soffice.exe'
        else:
            libreoffice_cmd = 'libreoffice'

        subprocess.run(
            [libreoffice_cmd, '--headless', '--convert-to', 'pdf', input_path, '--outdir', UPLOAD_FOLDER],
            check=True
        )

    return output_path
