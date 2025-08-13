# app/routes/merge.py
import os
import json
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


def _bool(v):
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _parse_pages_map(form, files_len):
    raw = form.get("pagesMap")
    if raw is None:
        raise BadRequest("Parâmetro 'pagesMap' é obrigatório.")
    try:
        pm = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de 'pagesMap' inválido (JSON).")

    if not isinstance(pm, list) or len(pm) != files_len:
        raise BadRequest("pagesMap deve ser lista de listas com o MESMO tamanho de 'files'.")

    for lst in pm:
        if not isinstance(lst, list) or not all(isinstance(p, int) for p in lst):
            raise BadRequest("pagesMap deve conter apenas listas de inteiros.")
    return pm


def _parse_rotations(form, files_len, pages_map):
    raw = form.get("rotations")
    if raw is None:
        # default: tudo zero, alinhado ao pages_map
        return [[0 for _ in pages] for pages in pages_map]
    try:
        rot = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de 'rotations' inválido (JSON).")

    if not isinstance(rot, list) or len(rot) != files_len:
        raise BadRequest("rotations deve ser lista de listas com o MESMO tamanho de 'files'.")

    for i, lst in enumerate(rot):
        if not isinstance(lst, list) or not all(isinstance(a, int) for a in lst):
            raise BadRequest("Cada item de 'rotations' deve ser lista de inteiros.")
        # não obrigo mesmo comprimento aqui; o serviço já tolera rotação faltante
    return rot


def _parse_crops(form, files_len):
    raw = form.get("crops")
    if not raw:
        return [[] for _ in range(files_len)]
    try:
        crops = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest("Formato de 'crops' inválido (JSON).")

    if not isinstance(crops, list) or len(crops) != files_len:
        raise BadRequest("crops deve ser lista de listas com o MESMO tamanho de 'files'.")

    for file_crops in crops:
        if not isinstance(file_crops, list):
            raise BadRequest("Cada elemento de 'crops' deve ser uma lista.")
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
    # 1) ARQUIVOS NA ORDEM DO DnD
    files = request.files.getlist("files")  # <- mantém a ordem que veio do FormData
    if not files:
        raise BadRequest("Nenhum arquivo enviado.")

    n = len(files)

    # 2) PARAMS / VALIDAÇÃO (alinhados ao nº de arquivos)
    pages_map = _parse_pages_map(request.form, n)
    rotations = _parse_rotations(request.form, n, pages_map)
    crops     = _parse_crops(request.form, n)

    # aceita snake e camel
    auto_orient = _bool(request.form.get("auto_orient") or request.form.get("autoOrient"))
    # flatten pode vir na query (?flatten=true) ou no form
    flatten = _bool(request.args.get("flatten") or request.form.get("flatten") or "true")
    pdf_settings = request.form.get("pdf_settings") or "/ebook"

    # 3) LOG de depuração — confirma a ordem recebida
    try:
        current_app.logger.debug(
            "[merge_route] files (order) = %s",
            [getattr(f, "filename", "?") for f in files]
        )
        current_app.logger.debug("[merge_route] pagesMap = %s", pages_map)
        current_app.logger.debug("[merge_route] rotations = %s", rotations)
        current_app.logger.debug("[merge_route] flatten=%s pdf_settings=%s auto_orient=%s",
                                 flatten, pdf_settings, auto_orient)
    except Exception:
        pass

    # 4) CHAMA O SERVIÇO — ele já salva temporários quando recebe FileStorage
    try:
        output_path = merge_selected_pdfs(
            file_paths=files,          # >>> ORDEM preservada
            pages_map=pages_map,       # >>> ORDEM por arquivo preservada
            rotations_map=rotations,
            flatten=flatten,
            pdf_settings=pdf_settings,
            auto_orient=auto_orient,
            crops=crops
        )

        # 5) RETORNO inline (o front decide baixar ou só pré-visualizar)
        resp = send_file(
            output_path,
            mimetype="application/pdf",
            as_attachment=False,
            download_name="merged.pdf",
            conditional=True,
        )
        # limpeza do arquivo após resposta ser gerada
        try:
            os.remove(output_path)
        except OSError:
            pass
        return resp

    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Erro interno ao juntar PDFs")
        return jsonify({"error": "Erro interno ao juntar PDFs."}), 500


@merge_bp.route("/merge", methods=["GET"])
def merge_form():
    return render_template("merge.html")


@merge_bp.route("/merge/preview", methods=["POST"])
def preview_merge():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    thumbs = preview_pdf(request.files["file"])
    return jsonify({"thumbnails": thumbs})