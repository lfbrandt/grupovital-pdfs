import os
import uuid
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import BadRequest
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload, secure_filename
from ..utils.pdf_utils import apply_pdf_modifications


def organize_pdf_service(
    pdf_file: FileStorage,
    pages: list,
    rotations: dict | None = None,
    crops: dict | None = None,
    strict: bool = True,
) -> str:
    """
    Salva o PDF enviado de forma segura e aplica as modificações pedidas.
    Retorna o caminho absoluto do PDF final.
    """
    if not pdf_file:
        raise BadRequest("Arquivo PDF é obrigatório.")

    # Valida MIME real
    validate_upload(pdf_file, allowed_extensions={".pdf"}, allowed_mimetypes={"application/pdf"})

    upload_dir = ensure_upload_folder_exists()
    safe_name = secure_filename(pdf_file.filename or f"input-{uuid.uuid4().hex}.pdf")
    input_path = os.path.join(upload_dir, f"{uuid.uuid4().hex}-{safe_name}")

    pdf_file.save(input_path)

    # Saída
    output_path = os.path.join(upload_dir, f"{uuid.uuid4().hex}-organizado.pdf")

    apply_pdf_modifications(
        input_path=input_path,
        output_path=output_path,
        pages=pages,
        rotations=rotations or {},
        crops=crops or {},
        strict=strict,
    )

    # Mantemos input para auditoria curta; limpeza global respeita UPLOAD_TTL_HOURS
    return output_path