from flask import (
    Blueprint,
    request,
    jsonify,
    send_file,
    render_template,
    after_this_request,
    abort,
    current_app,
)
import os
from ..services.merge_service import merge_selected_pdfs
from ..utils.preview_utils import preview_pdf
import json
from .. import limiter

merge_bp = Blueprint("merge", __name__)

# Limita este endpoint a no máximo 3 requisições por minuto por IP
@merge_bp.route("/merge", methods=["POST"])
@limiter.limit("3 per minute")
def merge():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    pages_json = request.form.get("pagesMap")
    if not pages_json:
        return jsonify({"error": "Faltam informações de páginas."}), 400
    try:
        pages_map = json.loads(pages_json)
    except json.JSONDecodeError:
        return jsonify({"error": "Formato de pagesMap inválido."}), 400

    if len(pages_map) != len(files):
        return jsonify({"error": "pagesMap não coincide com número de arquivos."}), 400

    rot_json = request.form.get("rotations")
    rotations = None
    if rot_json:
        try:
            rotations = json.loads(rot_json)
        except json.JSONDecodeError:
            return jsonify({"error": "Formato de rotations inválido."}), 400

    try:
        output_path = merge_selected_pdfs(files, pages_map, rotations)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)
    except Exception:
        current_app.logger.exception("Erro juntando PDFs")
        abort(500)

@merge_bp.route("/merge", methods=["GET"])
def merge_form():
    return render_template("merge.html")

@merge_bp.route("/merge/preview", methods=["POST"])
def preview_merge():
    """Return thumbnails for a PDF used in merge preview."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    file = request.files["file"]
    thumbs = preview_pdf(file)
    return jsonify({"thumbnails": thumbs})