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
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .. import limiter
from ..services.merge_service import merge_selected_pdfs
from ..utils.preview_utils import preview_pdf
from ..utils.config_utils import validate_upload  # compat múltiplas assinaturas
from ..utils.stats import record_job_event  # (7.1) métricas
from ..utils.limits import (
    enforce_total_files,   # limite de quantidade de arquivos
    enforce_total_pages,   # limite global de páginas
    count_pages,           # contagem por PDF
)

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
        if "rotation" in item and item["rotation"] is not None and not isinstance(item["rotation"], int):
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

    A) Plano FLAT recomendado: form field 'plan' (JSON array) com itens {src,page,rotation?,crop?}.
    B) Legado por arquivo: pagesMap/rotations/crops.

    Params opcionais:
      - auto_orient (bool), flatten (bool), pdf_settings (ex: '/ebook')
      - normalize: 'auto' | 'on' | 'off'  (DEFAULT: off)
      - norm_page_size: 'A4' | 'LETTER'
    """
    tmp_inputs = []
    try:
        files = request.files.getlist("files")
        if not files or len(files) < 2:
            raise BadRequest("Envie ao menos 2 arquivos PDF.")

        # Limite de quantidade de arquivos
        enforce_total_files(len(files), label="arquivos")

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

        auto_orient_param = request.form.get("auto_orient") or request.form.get("autoOrient")
        auto_orient = _bool(auto_orient_param) if auto_orient_param is not None else False

        # >>> ALTERAÇÃO: default do flatten agora é "false" (antes era "true")
        flatten      = _bool(request.args.get("flatten") or request.form.get("flatten") or "false")
        pdf_settings = request.form.get("pdf_settings") or "/ebook"

        # ► Normalização de tamanho de página (A4/Letter)
        # DEFAULT 'off' para evitar giros inesperados de PDFs com /Rotate
        normalize_mode = (request.form.get("normalize") or "off").strip().lower()   # auto|on|off
        norm_page_size = (request.form.get("norm_page_size") or "A4").strip().upper()  # A4|LETTER

        # salva uploads temporários e passa PATHS ao serviço
        for f in files:
            fd, path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            f.save(path)
            tmp_inputs.append(path)

        # --- DEDUPE DEFENSIVO (apenas quando NÃO há 'plan') ---
        # remove arquivos idênticos byte a byte, preservando a 1ª ocorrência
        if plan is None:
            import hashlib
            seen = {}
            filtered = []
            for _p in tmp_inputs:
                with open(_p, "rb") as _fh:
                    _digest = hashlib.sha256(_fh.read()).hexdigest()
                if _digest in seen:
                    current_app.logger.info(
                        "[merge_route] Dedupe: removendo duplicata de %s (igual a %s)",
                        os.path.basename(_p), os.path.basename(seen[_digest])
                    )
                    try:
                        os.remove(_p)
                    except OSError:
                        pass
                    continue
                seen[_digest] = _p
                filtered.append(_p)
            if len(filtered) != len(tmp_inputs):
                current_app.logger.info("[merge_route] Dedupe aplicado: %d -> %d arquivo(s).",
                                        len(tmp_inputs), len(filtered))
            tmp_inputs = filtered

        # ► Limite de páginas da operação (fail-fast)
        try:
            if isinstance(plan, list) and len(plan) >= 1:
                total_pages = len(plan)
            else:
                total_pages = 0
                if isinstance(pages_map, list):
                    for i, item in enumerate(pages_map):
                        if isinstance(item, list) and len(item) > 0:
                            total_pages += len(item)
                        else:
                            total_pages += count_pages(tmp_inputs[i])
                else:
                    for p in tmp_inputs:
                        total_pages += count_pages(p)

            enforce_total_pages(total_pages)
        except BadRequest:
            raise
        except Exception:
            current_app.logger.warning("Falha ao estimar total de páginas para merge.", exc_info=True)

        current_app.logger.debug(
            "[merge_route] files=%s | plan_items=%s | has_pagesMap=%s | flatten=%s | pdf_settings=%s | auto_orient=%s | normalize=%s | norm_page_size=%s",
            [os.path.basename(p) for p in tmp_inputs],
            (len(plan) if isinstance(plan, list) else 0),
            bool(pages_map),
            flatten, pdf_settings, auto_orient, normalize_mode, norm_page_size
        )

        output_path = merge_selected_pdfs(
            file_paths=tmp_inputs,
            plan=plan,                      # << usa plano FLAT quando houver
            pages_map=pages_map,
            rotations_map=rotations,
            flatten=flatten,
            pdf_settings=pdf_settings,
            auto_orient=auto_orient,
            crops=crops,
            normalize=normalize_mode,
            norm_page_size=norm_page_size,
        )

        # ===== (7.1) MÉTRICAS =====
        try:
            inputs_size = 0
            for p in tmp_inputs:
                try:
                    inputs_size += os.path.getsize(p)
                except Exception:
                    pass
            bytes_out = os.path.getsize(output_path) if os.path.exists(output_path) else None
            record_job_event(
                route="/api/merge",
                action="merge",
                bytes_in=(inputs_size if inputs_size > 0 else None),
                bytes_out=bytes_out,
                files_out=1,
            )
        except Exception:
            pass
        # ===========================

        @after_this_request
        def _cleanup(response):
            for p in tmp_inputs:
                try:
                    os.remove(p)
                except OSError:
                    pass
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

    except RequestEntityTooLarge:
        for p in tmp_inputs:
            try:
                os.remove(p)
            except OSError:
                pass
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        for p in tmp_inputs:
            try:
                os.remove(p)
            except OSError:
                pass
        return jsonify({"error": e.description or "Parâmetros inválidos."}), 422
    except Exception:
        for p in tmp_inputs:
            try:
                os.remove(p)
            except OSError:
                pass
        current_app.logger.exception("Erro interno ao juntar PDFs")
        return jsonify({"error": "Erro interno ao juntar PDFs."}), 500


@merge_bp.post("/preview")
@limiter.limit("10 per minute")
def preview_merge():
    try:
        if "file" not in request.files:
            return jsonify({"error": "Nenhum arquivo enviado."}), 400
        thumbs = preview_pdf(request.files["file"])
        return jsonify({"thumbnails": thumbs})
    except BadRequest as e:
        return jsonify({"error": e.description or "Requisição inválida."}), 422
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except Exception:
        current_app.logger.exception("Erro no preview de merge")
        return jsonify({"error": "Falha ao gerar preview."}), 500