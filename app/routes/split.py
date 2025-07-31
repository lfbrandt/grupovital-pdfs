from flask import (
    Blueprint,
    request,
    jsonify,
    send_file,
    render_template,
    current_app,
    after_this_request,
    abort,
)
from ..services.split_service import dividir_pdf
from ..utils.preview_utils import preview_pdf
import json
import os
import zipfile
import uuid
from .. import limiter

split_bp = Blueprint("split", __name__)

@split_bp.route("/split", methods=["POST"])
@limiter.limit("5 per minute")
def split():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado."}), 400

    # carrega lista de páginas (1-based) ou vazio para todas
    pages_json = request.form.get("pages", "[]")
    try:
        pages = json.loads(pages_json)
        pages = [int(p) for p in pages] if pages else None
    except (ValueError, TypeError):
        return jsonify({"error": "Formato de pages inválido. Deve ser um JSON de números."}), 400

    # carrega rotações ou vazio para nenhuma
    rot_json = request.form.get("rotations", "[]")
    try:
        rotations = json.loads(rot_json)
        rotations = [int(r) for r in rotations]
    except (ValueError, TypeError):
        return jsonify({"error": "Formato de rotations inválido. Deve ser um JSON de números."}), 400

    # carrega modificações extras (opcional)
    modificacoes = None
    mods = request.form.get("modificacoes")
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({"error": "Formato de modificacoes inválido. Deve ser JSON."}), 400

    try:
        pdf_paths = dividir_pdf(
            file,
            pages=pages,
            rotations=rotations,
            modificacoes=modificacoes,
        )

        # cria um ZIP com cada PDF gerado
        zip_filename = f"{uuid.uuid4().hex}.zip"
        zip_path = os.path.join(current_app.config["UPLOAD_FOLDER"], zip_filename)
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for path in pdf_paths:
                zipf.write(path, os.path.basename(path))

        @after_this_request
        def cleanup(response):
            try:
                os.remove(zip_path)
                for path in pdf_paths:
                    os.remove(path)
            except OSError:
                pass
            return response

        return send_file(zip_path, as_attachment=True)
    except Exception:
        current_app.logger.exception("Erro dividindo PDF")
        abort(500)


@split_bp.route("/split", methods=["GET"])
def split_form():
    return render_template("split.html")


@split_bp.route("/split/preview", methods=["POST"])
def preview_split():
    """Return thumbnails for um PDF usado no preview de split."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    file = request.files["file"]
    thumbs = preview_pdf(file)
    return jsonify({"thumbnails": thumbs})