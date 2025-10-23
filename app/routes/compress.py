# app/routes/compress.py
# -*- coding: utf-8 -*-
import os
import json
from flask import Blueprint, request, jsonify, send_file, after_this_request, current_app
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from ..services.compress_service import comprimir_pdf, USER_PROFILES
from .. import limiter
from ..utils.stats import record_job_event  # (7.1) métricas

compress_bp = Blueprint("compress", __name__, url_prefix="/api/compress")

# ------------------------ helpers ------------------------

def _normalize_profile(p: str) -> str:
    # .trim() não existe em Python; usar .strip()
    p = (p or "").strip().lower()
    allowed = set(USER_PROFILES.keys())  # {"equilibrio","mais-leve","alta-qualidade","sem-perdas"}
    return p if p in allowed else "equilibrio"

def _normalize_pages(pages_raw):
    """
    Aceita lista JSON de inteiros 1-based (ordem desejada).
    Suporta aliases: 'pages', 'order', 'page_order'.
    Retorna list[int] (1-based) ou None.
    """
    if not pages_raw:
        return None

    if isinstance(pages_raw, str):
        try:
            pages_raw = json.loads(pages_raw)
        except json.JSONDecodeError:
            raise BadRequest("pages/order deve ser JSON válido (lista de inteiros 1-based)")

    if pages_raw is None:
        return None
    if not isinstance(pages_raw, list):
        raise BadRequest("pages/order deve ser uma lista de inteiros (1-based)")

    out = []
    for p in pages_raw:
        try:
            n = int(p)
            if n >= 1:
                out.append(n)
        except Exception:
            raise BadRequest("pages/order deve conter apenas inteiros")
    return out or None

def _normalize_rotations(rot_raw):
    """
    Aceita:
      - dict {"1": 90, 5: 270, ...} (1-based, grau ABSOLUTO/extra)
      - list [0,90,0,270,...] (índice 0 => página 1)
    Suporta aliases: 'rotations', 'rot'
    Retorna dict[int,int] (1-based) com ângulos normalizados (0/90/180/270), omitindo 0.
    """
    if rot_raw is None or rot_raw == "":
        return None

    if isinstance(rot_raw, str):
        try:
            rot_raw = json.loads(rot_raw)
        except json.JSONDecodeError:
            raise BadRequest("rotations/rot deve ser JSON válido (lista ou objeto)")

    out = {}
    if isinstance(rot_raw, dict):
        for k, v in rot_raw.items():
            page_1b = int(k)  # chave 1-based
            deg = int(v) % 360
            if deg < 0:
                deg += 360
            if deg not in (0, 90, 180, 270):
                deg = (round(deg / 90) * 90) % 360
            if deg != 0:
                out[page_1b] = deg
    elif isinstance(rot_raw, list):
        for idx0, v in enumerate(rot_raw):
            deg = int(v) % 360
            if deg < 0:
                deg += 360
            if deg not in (0, 90, 180, 270):
                deg = (round(deg / 90) * 90) % 360
            page_1b = idx0 + 1
            if deg != 0:
                out[page_1b] = deg
    else:
        raise BadRequest("rotations/rot deve ser lista ou objeto JSON")

    return out or None

def _json_error(message: str, status: int = 400):
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp

# ------------------------ endpoints ------------------------

@compress_bp.route("", methods=["POST"])
@compress_bp.route("/", methods=["POST"])
@limiter.limit("5 per minute")
def compress():
    """
    Recebe:
      - file: PDF (obrigatório)
      - pages / order / page_order: JSON list[int] (1-based) com a ORDEM desejada — opcional
      - rotations / rot: JSON list[int] OU dict[str|int,int] — opcional (1-based, graus)
      - profile: str (mais-leve|equilibrio|alta-qualidade|sem-perdas) — opcional
      - modificacoes: JSON (opcional) — repassado ao serviço

    Retorna: PDF inline (para preview/download pelo front)
    """
    try:
        f = request.files.get("file")
        if not f or not f.filename:
            return _json_error("Nenhum arquivo enviado.", 400)

        profile = _normalize_profile(request.form.get("profile", "equilibrio"))

        modificacoes = None
        mods = request.form.get("modificacoes")
        if mods:
            try:
                modificacoes = json.loads(mods)
            except json.JSONDecodeError:
                return _json_error("modificacoes deve ser JSON válido", 422)

        try:
            pages = _normalize_pages(
                request.form.get("pages")
                or request.form.get("order")
                or request.form.get("page_order")
            )
        except BadRequest as e:
            return _json_error(e.description, 422)

        raw_rot = (
            request.form.get("rotations")
            or request.form.get("rot")
            or request.headers.get("X-Rotations")
        )
        try:
            rotations = _normalize_rotations(raw_rot)
        except BadRequest as e:
            return _json_error(e.description, 422)

        out_path = comprimir_pdf(
            f,
            pages=pages,
            rotations=rotations,
            modificacoes=modificacoes,
            profile=profile,
        )

        # ===== (7.1) MÉTRICAS =====
        try:
            bytes_out = os.path.getsize(out_path) if os.path.exists(out_path) else None
        except Exception:
            bytes_out = None
        try:
            bytes_in = int(request.content_length) if request.content_length else None
        except Exception:
            bytes_in = None
        try:
            record_job_event(
                route="/api/compress",
                action="compress",
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                files_out=1,
            )
        except Exception:
            pass
        # ===========================

        @after_this_request
        def _cleanup(resp):
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            return resp

        return send_file(
            out_path,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=os.path.basename(out_path),
        )

    except RequestEntityTooLarge:
        return _json_error("Arquivo muito grande (MAX_CONTENT_LENGTH).", 413)
    except BadRequest as br:
        return _json_error(br.description or "Requisição inválida.", 422)
    except Exception:
        current_app.logger.exception("Erro comprimindo PDF")
        return _json_error("Falha ao comprimir o PDF.", 500)


@compress_bp.get("/profiles")
def list_profiles():
    """Fornece rótulos e dicas para o front exibir as opções."""
    items = {k: {"label": v["label"], "hint": v["hint"]} for k, v in USER_PROFILES.items()}
    return jsonify(items)