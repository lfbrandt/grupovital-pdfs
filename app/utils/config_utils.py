import os

# Extensões de arquivo aceitas para conversão.
ALLOWED_EXTENSIONS = {
    'csv', 'docx', 'doc', 'html', 'jpg', 'jpeg', 'png',
    'xls', 'xlsx', 'odt', 'ods', 'odp',
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
