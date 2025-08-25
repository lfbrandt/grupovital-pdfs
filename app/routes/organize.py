import os
import json
from flask import (
    Blueprint, request, jsonify, send_file, render_template, after_this_request
)
from werkzeug.exceptions import BadRequest
from .. import limiter
from ..services.organize_service import organize_pdf_service

organize_bp = Blueprint("organize", __name__)


@organize_bp.get("/organize")
def page_organize():
    # Renderiza a tela com dropzone + grid
    return render_template("organize.html")


@organize_bp.post("/api/organize")
@limiter.limit("10 per minute")  # herda default; explícito aqui
def api_organize():
    """
    Espera multipart/form-data:
      - file: PDF
      - pages: JSON string de lista 1-based (ex.: [3,1,2])
      - rotations: JSON string de dict opcional {"3":90}
    """
    if "file" not in request.files:
        raise BadRequest("Envie o arquivo PDF em 'file'.")

    pdf_file = request.files["file"]

    pages_raw = request.form.get("pages")
    rotations_raw = request.form.get("rotations", "{}")

    try:
        pages = json.loads(pages_raw) if pages_raw else []
        rotations = json.loads(rotations_raw) if rotations_raw else {}
    except json.JSONDecodeError:
        raise BadRequest("Formato inválido para 'pages' ou 'rotations'.")

    if not isinstance(pages, list):
        raise BadRequest("'pages' deve ser uma lista de inteiros 1-based.")

    # Processa
    output_path = organize_pdf_service(
        pdf_file=pdf_file,
        pages=pages,
        rotations=rotations,
        crops=None,
        strict=True,
    )

    # Limpeza do arquivo após envio
    @after_this_request
    def _cleanup(response):
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        return response

    return send_file(
        output_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="organizado.pdf",
        conditional=True,
        max_age=0,
        last_modified=None,
    )