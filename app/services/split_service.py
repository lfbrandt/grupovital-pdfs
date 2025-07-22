import os
import uuid
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from .pdf_common import process_pdf_action


def dividir_pdf(file, pages=None, rotations=None, modificacoes=None):
    """Split a PDF into individual pages.

    Parameters
    ----------
    file : FileStorage
        The uploaded PDF file to split.
    pages : list[int] | None
        Optional list of page numbers (1-based) to split. If ``None`` all pages
        are emitted.
    rotations : list[int] | None
        Optional list of clockwise rotation angles to apply to each emitted
        page.  When shorter than ``pages`` the remaining pages receive ``0``
        rotation.
    modificacoes : dict | None
        Optional modifications (crop, rotate) applied to the entire document
        before splitting.
    """

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    input_path = process_pdf_action([file], modificacoes=modificacoes)[0]

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
                # aplica rotação de fato antes de adicionar a página
                try:
                    page.rotate_clockwise(angle)
                except Exception:
                    page.rotate(angle)

            writer = PdfWriter()
            writer.add_page(page)

            output_filename = f"pagina_{pageno}_{uuid.uuid4().hex}.pdf"
            output_path = os.path.join(upload_folder, output_filename)
            with open(output_path, "wb") as f_out:
                writer.write(f_out)

            output_files.append(output_path)

    os.remove(input_path)
    return output_files
