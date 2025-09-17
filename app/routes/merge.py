# app/routes/merge.py
# -*- coding: utf-8 -*-
from __future__ import annotations
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
    after_this_request,
)
from werkzeug.exceptions import BadRequest

from .. import limiter
from ..services.merge_service import merge_selected_pdfs
from ..utils.preview_utils import preview_pdf
from ..utils.config_utils import validate_upload  # compat múltiplas assinaturas

# Endpoints: /api/merge, /api/merge/, /api/merge/preview
merge_bp = Blueprint("merge", __name__, url_prefix="/api/merge")


def _bool(v):
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _parse_json_field(form, key, required_type, allow_empty=True):
    raw = form.get(key)
    if raw is None or str(raw).strip() == "":
        if allow_empty:
            return None
        raise BadRequest(f"Campo '{key}' é obrigatório.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest(f"Formato de '{key}' inválido (JSON).")

    if required_type == "list" and not isinstance(data, list):
        raise BadRequest(f"'{key}' deve ser lista.")
    if required_type == "dict" and not isinstance(data, dict):
        raise BadRequest(f"'{key}' deve ser objeto.")
    return data


def _parse_pages_map(form, files_len):
    pm = _parse_json_field(form, "pagesMap", "list", allow_empty=True)
    if pm is None:
        return None
    if len(pm) != files_len:
        raise BadRequest("pagesMap deve ter o MESMO tamanho de 'files'.")
    for lst in pm:
        if not isinstance(lst, list) or not all(isinstance(p, int) for p in lst):
            raise BadRequest("pagesMap deve conter apenas listas de inteiros.")
    return pm


def _parse_rotations(form, files_len):
    rot = _parse_json_field(form, "rotations", "list", allow_empty=True)
    if rot is None:
        return []
    if len(rot) != files_len:
        raise BadRequest("rotations deve ter o MESMO tamanho de 'files'.")
    for lst in rot:
        if not isinstance(lst, list) or not all(isinstance(a, int) for a in lst):
            raise BadRequest("Cada item de 'rotations' deve ser lista de inteiros.")
    return rot


def _parse_crops(form, files_len):
    crops = _parse_json_field(form, "crops", "list", allow_empty=True)
    if crops is None:
        return [[] for _ in range(files_len)]
    if len(crops) != files_len:
        raise BadRequest("crops deve ter o MESMO tamanho de 'files'.")
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


def _parse_flat_plan(form, files_len):
    """
    Formato linear (recomendado):
    plan = [
      {"src": 0, "page": 1, "rotation": 0, "crop": [x1,y1,x2,y2]},
      {"src": 1, "page": 3, "rotation": 90},
      ...
    ]
    - src ∈ [0..files_len-1]
    - page aceita 0-based OU 1-based; serviço normaliza.
    - rotation opcional (0/90/180/270).
    - crop opcional [x1,y1,x2,y2].
    """
    plan = _parse_json_field(form, "plan", "list", allow_empty=True)
    if plan is None:
        return None
    for i, item in enumerate(plan):
        if not isinstance(item, dict):
            raise BadRequest("Cada item do 'plan' deve ser um objeto.")
        if "src" not in item or "page" not in item:
            raise BadRequest("Cada item do 'plan' deve conter 'src' e 'page'.")
        src = item["src"]
        if not isinstance(src, int) or not (0 <= src < files_len):
            raise BadRequest(f"'src' inválido no item {i}.")
        if not isinstance(item["page"], int):
            raise BadRequest(f"'page' inválido no item {i}.")
        if "rotation" in item and not isinstance(item["rotation"], int):
            raise BadRequest(f"'rotation' deve ser inteiro no item {i}.")
        if "crop" in item:
            crop = item["crop"]
            if (
                not isinstance(crop, list)
                or len(crop) != 4
                or not all(isinstance(c, (int, float)) for c in crop)
            ):
                raise BadRequest(f"'crop' deve ser [x1,y1,x2,y2] no item {i}.")
    return plan


def _validate_pdf_upload(file_storage):
    """
    Compat com diversas assinaturas de validate_upload.
    Se nada servir, fallback: extensão .pdf e header %PDF-.
    """
    # 1) (file, allowed_exts=..., allowed_mimes=...)
    try:
        validate_upload(file_storage, allowed_exts={"pdf"}, allowed_mimes={"application/pdf"})  # type: ignore[arg-type]
        return
    except TypeError:
        pass
    except BadRequest:
        raise
    # 2) (file, {"pdf"}, {"application/pdf"})
    try:
        validate_upload(file_storage, {"pdf"}, {"application/pdf"})  # type: ignore[misc]
        return
    except TypeError:
        pass
    except BadRequest:
        raise
    # 3) (file) simples
    try:
        validate_upload(file_storage)
        return
    except TypeError:
        pass  # segue pra validação local

    # 4) fallback local
    name = (getattr(file_storage, "filename", "") or "").lower()
    if not name.endswith(".pdf"):
        raise BadRequest("Apenas arquivos .pdf são aceitos.")
    stream = file_storage.stream
    try:
        pos = stream.tell()
    except Exception:
        pos = None
    head = stream.read(5)
    try:
        if pos is not None:
            stream.seek(pos)
        else:
            stream.seek(0)
    except Exception:
        pass
    if not head or not head.startswith(b"%PDF-"):
        raise BadRequest("Arquivo enviado não parece um PDF válido.")


@merge_bp.route("", methods=["GET"])
@merge_bp.route("/", methods=["GET"])
@limiter.limit("10 per minute")
def merge_form():
    return render_template("merge.html")


@merge_bp.route("", methods=["POST"])
@merge_bp.route("/", methods=["POST"])
@limiter.limit("10 per minute")
def merge_api():
    """
    Recebe 'files' (>=2) NA ORDEM em que o front adicionou no FormData.

    Formatos aceitos (um OU outro):
    A) Novo formato FLAT (recomendado p/ DnD entre arquivos):
       - form field 'plan' (JSON array) com itens {src,page,rotation?,crop?}.

    B) Formato legado por arquivo:
       - pagesMap (JSON: lista por arquivo de páginas)
       - rotations (JSON: lista por arquivo de rotações)
       - crops (JSON: lista por arquivo de recortes {page,box})

    Outros params opcionais:
       - auto_orient (bool), flatten (bool), pdf_settings (ex: '/ebook')
    """
    files = request.files.getlist("files")
    if not files or len(files) < 2:
        raise BadRequest("Envie ao menos 2 arquivos PDF.")

    for f in files:
        _validate_pdf_upload(f)

    n = len(files)

    # ► PRIORIDADE: usar plano linear se presente
    plan = _parse_flat_plan(request.form, n)

    # ► Legado (só usado se 'plan' for None)
    pages_map = rotations = crops = None
    if plan is None:
        pages_map = _parse_pages_map(request.form, n)
        rotations = _parse_rotations(request.form, n)
        crops     = _parse_crops(request.form, n)

    # ► Default de auto_orient:
    # Se front não especificar, padroniza False (não interferir na rotação da UI)
    auto_orient_param = request.form.get("auto_orient") or request.form.get("autoOrient")
    auto_orient = _bool(auto_orient_param) if auto_orient_param is not None else False

    flatten      = _bool(request.args.get("flatten") or request.form.get("flatten") or "true")
    pdf_settings = request.form.get("pdf_settings") or "/ebook"

    # salva uploads temporários e passa PATHS ao serviço
    tmp_inputs = []
    try:
        for f in files:
            fd, path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            f.save(path)
            tmp_inputs.append(path)

        current_app.logger.debug(
            "[merge_route] files=%s | plan_items=%s | has_pagesMap=%s | flatten=%s | pdf_settings=%s | auto_orient=%s",
            [os.path.basename(p) for p in tmp_inputs],
            (len(plan) if isinstance(plan, list) else 0),
            bool(pages_map),
            flatten, pdf_settings, auto_orient
        )

        output_path = merge_selected_pdfs(
            file_paths=tmp_inputs,
            plan=plan,                      # << ESSENCIAL: envia o plano >>
            pages_map=pages_map,
            rotations_map=rotations,
            flatten=flatten,
            pdf_settings=pdf_settings,
            auto_orient=auto_orient,
            crops=crops
        )

        @after_this_request
        def _cleanup(response):
            for p in tmp_inputs:
                try: os.remove(p)
                except OSError: pass
            try:
                if output_path and os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(
            output_path,
            mimetype="application/pdf",
            as_attachment=False,
            download_name="merged.pdf",
            conditional=True,
            max_age=0
        )

    except BadRequest as e:
        for p in tmp_inputs:
            try: os.remove(p)
            except OSError: pass
        return jsonify({"error": str(e)}), 400
    except Exception:
        for p in tmp_inputs:
            try: os.remove(p)
            except OSError: pass
        current_app.logger.exception("Erro interno ao juntar PDFs")
        return jsonify({"error": "Erro interno ao juntar PDFs."}), 500


@merge_bp.post("/preview")
@limiter.limit("10 per minute")
def preview_merge():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    thumbs = preview_pdf(request.files["file"])
    return jsonify({"thumbnails": thumbs})