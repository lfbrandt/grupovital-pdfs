import os
import uuid
from flask import current_app
from PyPDF2 import PdfMerger
from PyPDF2 import PdfReader, PdfWriter
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications


def juntar_pdfs(files, modificacoes=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    merger = PdfMerger()
    filenames = []

    for file in files:
        filename = validate_upload(file, {"pdf"})
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


def extrair_paginas_pdf(file, pages, rotations=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    filename = validate_upload(file, {"pdf"})
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_name)
    file.save(input_path)

    reader = PdfReader(input_path)
    writer = PdfWriter()
    rotations = rotations or []
    for idx, p in enumerate(pages):
        if 1 <= p <= len(reader.pages):
            page = reader.pages[p - 1]
            angle = rotations[idx] if idx < len(rotations) else 0
            if angle:
                try:
                    page.rotate_clockwise(angle)
                except Exception:
                    page.rotate(angle)
            writer.add_page(page)

    output_filename = f"selected_{uuid.uuid4().hex}.pdf"
    output_path = os.path.join(upload_folder, output_filename)
    with open(output_path, "wb") as f_out:
        writer.write(f_out)

    try:
        os.remove(input_path)
    except OSError:
        pass

    return output_path


def merge_pdfs(file_list):
    writer = PdfWriter()
    for file in file_list:
        reader = PdfReader(file)
        for page in reader.pages:
            writer.add_page(page)
    out_folder = current_app.config["UPLOAD_FOLDER"]
    out_name = f"merge_{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(out_folder, out_name)
    with open(out_path, "wb") as f:
        writer.write(f)
    return out_path


def merge_selected_pdfs(file_list, pages_map, rotations_map=None):
    """Merge PDFs using a list of page lists (1-based)."""
    writer = PdfWriter()
    rotations_map = rotations_map or []
    for idx, file in enumerate(file_list):
        reader = PdfReader(file)
        pages = pages_map[idx] if idx < len(pages_map) else None
        rots = rotations_map[idx] if idx < len(rotations_map) else []
        if pages is None:
            for j, p in enumerate(reader.pages):
                angle = rots[j] if j < len(rots) else 0
                if angle:
                    try:
                        p.rotate_clockwise(angle)
                    except Exception:
                        p.rotate(angle)
                writer.add_page(p)
        else:
            for j, pnum in enumerate(pages):
                if 1 <= pnum <= len(reader.pages):
                    page = reader.pages[pnum - 1]
                    angle = rots[j] if j < len(rots) else 0
                    if angle:
                        try:
                            page.rotate_clockwise(angle)
                        except Exception:
                            page.rotate(angle)
                    writer.add_page(page)
    out_folder = current_app.config["UPLOAD_FOLDER"]
    out_name = f"merge_{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(out_folder, out_name)
    with open(out_path, "wb") as f:
        writer.write(f)
    return out_path
