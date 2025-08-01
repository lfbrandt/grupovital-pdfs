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


def _parse_and_validate_rotations(form, files):
    rotations_raw = form.get("rotations")
    if rotations_raw is None:
        # default zeros for each file
        return [[0] * len(pages) for pages in _parse_and_validate_pages_map(form, files)]
    try:
        rotations = json.loads(rotations_raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de rotations inválido.")
    if (
        not isinstance(rotations, list)
        or len(rotations) != len(files)
        or not all(isinstance(lst, list) and all(isinstance(r, int) for r in lst)
                   for lst in rotations)
    ):
        raise BadRequest("rotations deve ser lista de listas de inteiros com mesmo tamanho de 'files'.")
    return rotations


def _parse_and_validate_crops(form, files):
    crops_raw = form.get("crops")
    if not crops_raw:
        # no crops
        return [[] for _ in files]
    try:
        crops = json.loads(crops_raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de crops inválido.")
    if not isinstance(crops, list) or len(crops) != len(files):
        raise BadRequest("crops deve ser lista de listas com mesmo tamanho de 'files'.")
    for file_crops in crops:
        if not isinstance(file_crops, list):
            raise BadRequest("Cada elemento de crops deve ser uma lista.")
        for rec in file_crops:
            if not isinstance(rec, dict) or 'page' not in rec or 'box' not in rec:
                raise BadRequest("Cada recorte deve ser dict com 'page' e 'box'.")
            if not isinstance(rec['page'], int):
                raise BadRequest("'page' em crops deve ser inteiro.")
            box = rec['box']
            if (
                not isinstance(box, list)
                or len(box) != 4
                or not all(isinstance(coord, (int, float)) for coord in box)
            ):
                raise BadRequest("'box' em crops deve ser lista de 4 números.")
    return crops


@merge_bp.route("/merge", methods=["POST"])
@limiter.limit("3 per minute")
def merge():
    # 1. Recebe arquivos
    files = request.files.getlist("files")
    if not files:
        raise BadRequest("Nenhum arquivo enviado.")

    # 2. Valida pagesMap, rotations, auto-orient e crops
    pages_map = _parse_and_validate_pages_map(request.form, files)
    rotations = _parse_and_validate_rotations(request.form, files)
    auto_orient = request.form.get("autoOrient", "false").lower() == "true"
    crops = _parse_and_validate_crops(request.form, files)

    # 3. Parâmetro opcional ?flatten=false do query string
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

        current_app.logger.info(
            f"Merging {len(files)} arquivos, flatten={flatten}, auto_orient={auto_orient}"
        )

        # 5. Chama o serviço com novos parâmetros
        output_path = merge_selected_pdfs(
            temp_inputs,
            pages_map,
            rotations,
            flatten=flatten,
            auto_orient=auto_orient,
            crops=crops
        )

        # 6. Retorna o PDF final
        return send_file(output_path, as_attachment=True, conditional=True)

    except BadRequest:
        # dispara o 400
        raise

    except Exception:
        current_app.logger.exception("Erro interno ao juntar PDFs")
        return jsonify({"error": "Erro interno ao juntar PDFs."}), 500

    finally:
        # 7. Limpa temporários
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