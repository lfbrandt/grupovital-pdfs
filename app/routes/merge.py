import os
import json
import tempfile
from flask import (
    Blueprint,
    request,
    jsonify,
    send_file,
    render_template,
    current_app,
)
from werkzeug.exceptions import BadRequest
from ..services.merge_service import merge_selected_pdfs
from ..utils.preview_utils import preview_pdf
from .. import limiter

merge_bp = Blueprint("merge", __name__)


def _cleanup_paths(paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _parse_and_validate_pages_map(form, files):
    pages_map_raw = form.get("pagesMap")
    if pages_map_raw is None:
        raise BadRequest("Parâmetro 'pagesMap' é obrigatório.")
    try:
        pages_map = json.loads(pages_map_raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de pagesMap inválido.")
    if not isinstance(pages_map, list) or len(pages_map) != len(files):
        raise BadRequest("pagesMap deve ser lista de listas com mesmo tamanho de 'files'.")
    for lst in pages_map:
        if not isinstance(lst, list) or not all(isinstance(p, int) for p in lst):
            raise BadRequest("pagesMap deve conter apenas listas de inteiros.")
    return pages_map


def _parse_and_validate_rotations(form):
    rotations_raw = form.get("rotations")
    if rotations_raw is None:
        return None
    try:
        rotations = json.loads(rotations_raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de rotations inválido.")
    if (
        not isinstance(rotations, list)
        or not all(isinstance(lst, list) and all(isinstance(r, int) for r in lst)
                   for lst in rotations)
    ):
        raise BadRequest("rotations deve ser lista de listas de inteiros.")
    return rotations


@merge_bp.route("/merge", methods=["POST"])
@limiter.limit("3 per minute")
def merge():
    # 1. Recebe arquivos
    files = request.files.getlist("files")
    if not files:
        raise BadRequest("Nenhum arquivo enviado.")

    # 2. Valida pagesMap e rotations
    pages_map = _parse_and_validate_pages_map(request.form, files)
    rotations = _parse_and_validate_rotations(request.form)

    # 3. Parâmetro opcional ?flatten=false
    flatten = request.args.get("flatten", "true").lower() != "false"

    temp_inputs = []
    output_path = None

    try:
        # 4. Grava cada PDF num temporário
        for f in files:
            tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            f.save(tf.name)
            temp_inputs.append(tf.name)
            tf.close()

        current_app.logger.info(f"Merging {len(files)} arquivos, flatten={flatten}")

        # 5. Chama o serviço com flatten opcional
        output_path = merge_selected_pdfs(
            temp_inputs,
            pages_map,
            rotations,
            flatten=flatten  # utiliza o novo parâmetro
        )

        # 6. Retorna o PDF final
        return send_file(output_path, as_attachment=True, conditional=True)

    except BadRequest as e:
        # dispara o 400
        raise

    except Exception:
        current_app.logger.exception("Erro interno ao juntar PDFs")
        return jsonify({"error": "Erro interno ao juntar PDFs."}), 500

    finally:
        # 7. Limpa todos os temporários
        _cleanup_paths(temp_inputs)
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass


@merge_bp.route("/merge", methods=["GET"])
def merge_form():
    return render_template("merge.html")


@merge_bp.route("/merge/preview", methods=["POST"])
def preview_merge():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    thumbs = preview_pdf(request.files["file"])
    return jsonify({"thumbnails": thumbs})