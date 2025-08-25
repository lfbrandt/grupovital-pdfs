# app/routes/split.py
# -*- coding: utf-8 -*-
import os
import uuid
import json
import zipfile
from flask import (
    Blueprint, request, jsonify, send_file,
    render_template, current_app, after_this_request, abort
)
from werkzeug.exceptions import BadRequest
from ..services.split_service import dividir_pdf
from ..utils.preview_utils import preview_pdf
from .. import limiter

# Padrão A: url_prefix completo
split_bp = Blueprint("split", __name__, url_prefix="/api/split")

def _parse_pages(raw):
    """Aceita JSON [1,2] ou string '1,2,4-6'. Retorna lista[int] ou None (todas)."""
    if raw is None or raw == "":
        return None
    # tenta JSON primeiro
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [int(p) for p in parsed if str(p).strip()]
    except Exception:
        pass
    # fallback: "1,2,4-6"
    pages = []
    try:
        parts = str(raw).replace(" ", "").split(",")
        for part in parts:
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                if a <= 0 or b <= 0 or b < a:
                    raise BadRequest("Faixa de páginas inválida.")
                pages.extend(range(a, b + 1))
            else:
                n = int(part)
                if n <= 0:
                    raise BadRequest("Número de página inválido.")
                pages.append(n)
    except BadRequest:
        raise
    except Exception:
        raise BadRequest("Formato de páginas inválido.")
    # remove duplicadas preservando ordem
    seen = set(); out = []
    for p in pages:
        if p not in seen:
            seen.add(p); out.append(p)
    return out or None

def _parse_rotations(raw):
    """Aceita JSON dict/list ou string '0,90,0'. Retorna dict[str->int]."""
    if raw is None or raw == "":
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): int(v) for k, v in parsed.items()}
        if isinstance(parsed, list):
            return {str(i + 1): int(v) for i, v in enumerate(parsed)}
    except Exception:
        pass
    # fallback simples: "0,90,0"
    parts = str(raw).split(",")
    rot = {}
    try:
        for i, r in enumerate(parts):
            r = r.strip()
            if not r:
                continue
            rot[str(i + 1)] = int(r)
    except Exception:
        raise BadRequest("Formato de rotations inválido.")
    return rot

# POST /api/split   (com e sem barra)
@split_bp.route("", methods=["POST"])
@split_bp.route("/", methods=["POST"])
@limiter.limit("5 per minute")
def split():
    try:
        # 1) Arquivo
        if "file" not in request.files:
            return jsonify({"error": "Nenhum arquivo enviado."}), 400
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Nenhum arquivo selecionado."}), 400

        # 2) Params
        pages = _parse_pages(request.form.get("pages"))
        rotations = _parse_rotations(request.form.get("rotations"))

        # Aceitar tanto 'modificacoes' (pt) quanto 'modifications' (en)
        modificacoes = None
        raw_mods = request.form.get("modificacoes") or request.form.get("modifications")
        if raw_mods:
            try:
                modificacoes = json.loads(raw_mods)
            except json.JSONDecodeError:
                return jsonify({"error": "Formato de modificacoes inválido. Deve ser JSON."}), 400

        # 3) Serviço
        pdf_paths = dividir_pdf(
            file,
            pages=pages,
            rotations=rotations,
            modificacoes=modificacoes,
        )

        # 4) Resposta: se selecionei páginas -> 1 PDF; senão -> ZIP
        if pages:  # lista não vazia
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
                download_name="paginas_selecionadas.pdf",
                mimetype="application/pdf",
            )

        # Sem pages => ZIP com todas as páginas
        zip_filename = f"{uuid.uuid4().hex}.zip"
        zip_path = os.path.join(current_app.config["UPLOAD_FOLDER"], zip_filename)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
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

        return send_file(
            zip_path,
            as_attachment=True,
            download_name="paginas_divididas.zip",
            mimetype="application/zip",
        )

    except BadRequest as e:
        return jsonify({"error": e.description or "Requisição inválida."}), 400
    except Exception:
        current_app.logger.exception("Erro dividindo PDF")
        abort(500)

# GET /api/split  (form HTML da ferramenta)
@split_bp.route("", methods=["GET"])
@split_bp.route("/", methods=["GET"])
def split_form():
    return render_template("split.html")

# POST /api/split/preview  (thumbs)
@split_bp.post("/preview")
def preview_split():
    """Return thumbnails para preview de split."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    file = request.files["file"]
    thumbs = preview_pdf(file)
    return jsonify({"thumbnails": thumbs})