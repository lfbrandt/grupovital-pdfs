import os
import uuid
from flask import current_app
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications


def process_pdf_action(files, *, pages_map=None, rotations=None, modificacoes=None):
    """Save uploaded PDFs applying optional modifications.

    Returns a list of file paths for further processing."""
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    paths = []
    for idx, file in enumerate(files):
        filename = validate_upload(file, {"pdf"})
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        path = os.path.join(upload_folder, unique_name)
        file.save(path)
        apply_pdf_modifications(path, modificacoes)
        paths.append(path)
    return paths
