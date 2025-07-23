import os
import mimetypes
import time
from werkzeug.utils import secure_filename

# Extensões de arquivo aceitas para conversão.
ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'html',
    'xls', 'xlsx', 'ods',
    'ppt', 'pptx', 'odp',
    'jpg', 'jpeg', 'png', 'bmp', 'tiff',
}


def allowed_file(filename):
    """Retorna ``True`` se o arquivo possuir uma extensão permitida."""
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )

def ensure_upload_folder_exists(upload_folder):
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)


def validate_upload(file, allowed_extensions):
    """Valida extensao e MIME type antes do processamento."""
    filename = secure_filename(file.filename)
    if '.' not in filename:
        raise Exception('Extensão de arquivo inválida.')
    ext = filename.rsplit('.', 1)[1].lower()
    if ext not in allowed_extensions:
        raise Exception('Extensão de arquivo não suportada.')

    guessed = mimetypes.guess_type(filename)[0]
    mimetype = getattr(file, 'mimetype', None)
    if mimetype and mimetype not in ('application/octet-stream', guessed):
        raise Exception('Tipo MIME inválido.')

    return filename


def clean_old_uploads(upload_folder, max_age_hours):
    """Remove arquivos mais antigos que ``max_age_hours`` horas."""
    now = time.time()
    cutoff = now - max_age_hours * 3600
    if not os.path.isdir(upload_folder):
        return
    for name in os.listdir(upload_folder):
        path = os.path.join(upload_folder, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass

