import os
import uuid
from flask import current_app
from PyPDF2 import PdfMerger
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.utils import secure_filename
from ..utils.config_utils import ensure_upload_folder_exists
from ..utils.pdf_utils import apply_pdf_modifications


def juntar_pdfs(files, modificacoes=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    merger = PdfMerger()
    filenames = []

    for file in files:
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".pdf"):
            raise Exception("Apenas arquivos PDF são permitidos.")
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        path = os.path.join(upload_folder, unique_filename)
        file.save(path)
        apply_pdf_modifications(path, modificacoes)
        filenames.append(path)
        merger.append(path)

    output_filename = f"merged_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_filename)
    merger.write(output_path)
    merger.close()

    for f in filenames:
        try:
            os.remove(f)
        except OSError:
            pass

    return output_path


def extrair_paginas_pdf(file, pages):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        raise Exception("Apenas arquivos PDF são permitidos.")
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_name)
    file.save(input_path)

    reader = PdfReader(input_path)
    writer = PdfWriter()
    for p in pages:
        if 1 <= p <= len(reader.pages):
            writer.add_page(reader.pages[p - 1])

    output_filename = f"selected_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_filename)
    with open(output_path, "wb") as f_out:
        writer.write(f_out)

    try:
        os.remove(input_path)
    except OSError:
        pass

    return output_path
