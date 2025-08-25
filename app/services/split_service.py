# app/services/split_service.py
import os
import uuid
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.exceptions import BadRequest

from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications
from ..utils.limits import enforce_pdf_page_limit, enforce_total_pages

def dividir_pdf(file, pages=None, rotations=None, modificacoes=None):
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    filename = validate_upload(file, {"pdf"})
    in_name = f"{uuid.uuid4().hex}_{filename}"
    in_path = os.path.join(upload_folder, in_name)
    file.save(in_path)

    try:
        enforce_pdf_page_limit(in_path, label=filename)

        reader = PdfReader(in_path)
        total = len(reader.pages)

        if pages:
            pages_to_emit = [int(p) for p in pages if 1 <= int(p) <= total]
            if not pages_to_emit:
                raise BadRequest("Nenhuma página válida foi selecionada.")
        else:
            pages_to_emit = list(range(1, total + 1))

        enforce_total_pages(len(pages_to_emit))

        # normaliza rotações recebidas (chaves podem vir str)
        rot_map = {}
        if isinstance(rotations, dict):
            for k, v in rotations.items():
                try:
                    kk, vv = int(k), int(v)
                    rot_map[kk] = vv % 360
                except Exception:
                    continue

        # normaliza modificacoes: dict {page: {...}}
        mods_map = {}
        if isinstance(modificacoes, dict):
            for k, v in modificacoes.items():
                try:
                    mods_map[int(k)] = v
                except Exception:
                    continue

        output_files = []

        if pages:
            writer = PdfWriter()
            for p in pages_to_emit:
                page = reader.pages[p - 1]
                angle = rot_map.get(p, 0)
                if angle:
                    try: page.rotate(angle)
                    except Exception: page.rotate_clockwise(angle)

                # ✅ aplica SOMENTE as modificações dessa página
                mods = mods_map.get(p)
                if mods:
                    apply_pdf_modifications(page, modificacoes=mods)

                writer.add_page(page)

            out_name = f"selecionadas_{uuid.uuid4().hex}.pdf"
            out_path = os.path.join(upload_folder, out_name)
            with open(out_path, "wb") as f_out:
                writer.write(f_out)
            output_files.append(out_path)
        else:
            for p in pages_to_emit:
                page = reader.pages[p - 1]
                angle = rot_map.get(p, 0)
                if angle:
                    try: page.rotate(angle)
                    except Exception: page.rotate_clockwise(angle)

                mods = mods_map.get(p)
                if mods:
                    apply_pdf_modifications(page, modificacoes=mods)

                writer = PdfWriter()
                writer.add_page(page)

                out_name = f"pagina_{p}_{uuid.uuid4().hex}.pdf"
                out_path = os.path.join(upload_folder, out_name)
                with open(out_path, "wb") as f_out:
                    writer.write(f_out)

                output_files.append(out_path)

        return output_files
    finally:
        try: os.remove(in_path)
        except OSError: pass