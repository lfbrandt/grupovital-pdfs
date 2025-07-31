import os
import uuid
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications

def dividir_pdf(file, pages=None, rotations=None, modificacoes=None):
    """Split a PDF into individual pages, applying clockwise rotations."""

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    filename = validate_upload(file, {"pdf"})
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)
    apply_pdf_modifications(input_path, modificacoes)

    reader = PdfReader(input_path)
    rotations = rotations or []
    output_files = []

    # Determine pages to emit (1-based numbers)
    total_pages = len(reader.pages)
    pages_to_emit = pages if pages else list(range(1, total_pages + 1))

    for idx, pageno in enumerate(pages_to_emit):
        if 1 <= pageno <= total_pages:
            page = reader.pages[pageno - 1]
            angle = rotations[idx] if idx < len(rotations) else 0
            if angle:
                try:
                    # Gira no sentido horário para bater com o preview
                    page.rotate_clockwise(angle)
                except AttributeError:
                    # Fallback para versões antigas do PyPDF2
                    page.rotate(angle)

            writer = PdfWriter()
            writer.add_page(page)

            output_filename = f"pagina_{pageno}_{uuid.uuid4().hex}.pdf"
            output_path = os.path.join(upload_folder, output_filename)
            with open(output_path, "wb") as f_out:
                writer.write(f_out)

            output_files.append(output_path)

    try:
        os.remove(input_path)
    except OSError:
        pass

    return output_files