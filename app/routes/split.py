import os
import uuid
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
import zipfile
from .. import limiter

split_bp = Blueprint("split", __name__)

@split_bp.route("/split", methods=["POST"])
@limiter.limit("5 per minute")
def split():
    # 1) Arquivo
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado."}), 400

    # 2) Páginas (JSON [1,2,3] ou string "1,2,3")
    raw_pages = request.form.get("pages", "")
    pages = None
    if raw_pages:
        try:
            parsed = json.loads(raw_pages)
            pages = [int(p) for p in parsed]
        except (ValueError, TypeError):
            pages = [int(p) for p in raw_pages.split(",") if p.strip().isdigit()]

    # 3) Rotações
    raw_rots = request.form.get("rotations", "")
    rotations = {}
    if raw_rots:
        try:
            parsed = json.loads(raw_rots)
            if isinstance(parsed, dict):
                rotations = {str(k): int(v) for k, v in parsed.items()}
            elif isinstance(parsed, list):
                rotations = {str(i+1): int(parsed[i]) for i in range(len(parsed))}
        except (ValueError, TypeError):
            parts = raw_rots.split(",")
            rotations = {
                str(i+1): int(r)
                for i, r in enumerate(parts)
                if r.strip().isdigit()
            }

    # 4) Modificações extras (opcional)
    modificacoes = None
    raw_mods = request.form.get("modificacoes", "")
    if raw_mods:
        try:
            modificacoes = json.loads(raw_mods)
        except json.JSONDecodeError:
            return jsonify({"error": "Formato de modificacoes inválido. Deve ser JSON."}), 400

    try:
        # chama o serviço
        pdf_paths = dividir_pdf(
            file,
            pages=pages,
            rotations=rotations,
            modificacoes=modificacoes,
        )

        # Caso tenha selecionado páginas, retorna um único PDF
        if pages:
            output_path = pdf_paths[0]
            @after_this_request
            def cleanup_single(response):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                return response

            return send_file(
                output_path,
                as_attachment=True,
                download_name="paginas_selecionadas.pdf"
            )

        # Caso padrão (sem pages): gera ZIP com cada PDF separado
        zip_filename = f"{uuid.uuid4().hex}.zip"
        zip_path = os.path.join(current_app.config["UPLOAD_FOLDER"], zip_filename)
        with zipfile.ZipFile(zip_path, "w") as zipf:
            for path in pdf_paths:
                zipf.write(path, os.path.basename(path))

        @after_this_request
        def cleanup_zip(response):
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
    """Return thumbnails para preview de split."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    file = request.files["file"]
    thumbs = preview_pdf(file)
    return jsonify({"thumbnails": thumbs})