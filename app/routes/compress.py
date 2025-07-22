# app/routes/compress.py

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
import json
from ..services.compress_service import comprimir_pdf, preview_pdf
from .. import limiter

compress_bp = Blueprint("compress", __name__, url_prefix="/compress")


@compress_bp.route("", methods=["GET"])
def compress_form():
    """Renderiza a página de compressão."""
    return render_template("compress.html")


@compress_bp.route("", methods=["POST"], endpoint="compress")
@limiter.limit("5 per minute")
def do_compress():
    """Recebe o PDF, as modificações (rotação/exclusão) e comprime."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado."}), 400

    level = request.form.get("level", "ebook")

    # Lê o JSON de modifications (remove + rotate)
    mods = {}
    mods_field = request.form.get("modifications")
    if mods_field:
        try:
            mods = json.loads(mods_field)
        except json.JSONDecodeError:
            return jsonify({"error": "`modifications` deve ser JSON válido."}), 400

    try:
        output_path = comprimir_pdf(file, level=level, mods=mods)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)

    except Exception as e:
        current_app.logger.exception("Erro comprimindo PDF")
        return jsonify({"error": str(e)}), 500


@compress_bp.route("/preview", methods=["POST"])
@limiter.limit("5 per minute")
def compress_preview():
    """Gera data-URIs PNG das páginas para pré-visualização no front-end."""
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Envie um PDF válido."}), 400

    try:
        pages = preview_pdf(file)
        return jsonify({"pages": pages})
    except Exception:
        current_app.logger.exception("Erro gerando preview")
        return jsonify({"error": "Erro ao gerar preview."}), 500
