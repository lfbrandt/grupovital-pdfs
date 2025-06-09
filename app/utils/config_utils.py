import os

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'csv','docx', 'doc', 'html', 'jpg', 'jpeg','png', 'xls', 'xlsx', 'odt', 'ods', 'odp', }

    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ensure_upload_folder_exists(upload_folder):
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)
