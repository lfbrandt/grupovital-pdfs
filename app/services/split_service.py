import os
import uuid
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications

def dividir_pdf(file, pages=None, rotations=None, modificacoes=None):
    """
    Se pages=None: gera um PDF individual para cada página (com rotações e modificações).
    Se pages for lista de ints: gera UM ÚNICO PDF contendo só essas páginas, na ordem dada.
    Retorna sempre uma lista de caminhos (no caso único, lista com 1 elemento).
    """
    # 1) Preparar pasta
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    # 2) Salvar input
    filename = validate_upload(file, {"pdf"})
    in_name = f"{uuid.uuid4().hex}_{filename}"
    in_path = os.path.join(upload_folder, in_name)
    file.save(in_path)

    # 3) Ler PDF
    reader = PdfReader(in_path)
    total = len(reader.pages)

    # 4) Normalizar páginas
    if pages:
        pages_to_emit = [p for p in pages if 1 <= p <= total]
    else:
        pages_to_emit = list(range(1, total + 1))

    # 5) Normalizar rotações
    rots = {}
    if isinstance(rotations, dict):
        # já mapeado página → ângulo
        for k, v in rotations.items():
            rots[str(k)] = int(v)
    elif isinstance(rotations, list):
        # se páginas foram filtradas, relaciona rotações em ordem
        if pages:
            for idx, ang in enumerate(rotations):
                if idx < len(pages_to_emit):
                    rots[str(pages_to_emit[idx])] = int(ang)
        else:
            # mapeia lista direta (página 1 → rotations[0], etc)
            for i, ang in enumerate(rotations):
                rots[str(i+1)] = int(ang)

    output_files = []

    # 6) Gerar um único PDF se pages especificadas
    if pages:
        writer = PdfWriter()
        for p in pages_to_emit:
            page = reader.pages[p-1]
            angle = rots.get(str(p), 0)
            if angle:
                page.rotate(angle)
            if modificacoes:
                apply_pdf_modifications(page, modificacoes=modificacoes)
            writer.add_page(page)

        out_name = f"selecionadas_{uuid.uuid4().hex}.pdf"
        out_path = os.path.join(upload_folder, out_name)
        with open(out_path, "wb") as f_out:
            writer.write(f_out)
        output_files.append(out_path)

    else:
        # 7) Caso padrão: um PDF por página
        for p in pages_to_emit:
            page = reader.pages[p-1]
            angle = rots.get(str(p), 0)
            if angle:
                page.rotate(angle)
            if modificacoes:
                apply_pdf_modifications(page, modificacoes=modificacoes)

            writer = PdfWriter()
            writer.add_page(page)

            out_name = f"pagina_{p}_{uuid.uuid4().hex}.pdf"
            out_path = os.path.join(upload_folder, out_name)
            with open(out_path, "wb") as f_out:
                writer.write(f_out)

            output_files.append(out_path)

    # 8) Remover input
    try:
        os.remove(in_path)
    except OSError:
        pass

    return output_files