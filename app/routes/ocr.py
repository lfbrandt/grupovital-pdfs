# app/routes/ocr.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from flask import Blueprint, request, jsonify, send_file, after_this_request
from werkzeug.exceptions import BadRequest

from .. import limiter
from ..services.ocr_service import ocr_upload_file

ocr_bp = Blueprint("ocr", __name__, url_prefix="/api/ocr")

def _b(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}

@ocr_bp.route("", methods=["POST"])
@ocr_bp.route("/", methods=["POST"])
@limiter.limit("5 per minute")
def ocr_endpoint():
    """
    multipart/form-data:
      - file (PDF)
      - lang (opcional, ex. "por+eng")
      - force (bool) — força OCR mesmo se já tiver texto
      - skip_text (bool) — ignora páginas com texto (padrão: true)
      - optimize (0..3) — nível de otimização (padrão: 2)
      - deskew (bool, padrão true), rotate_pages (bool, padrão true), clean (bool, padrão true)
      - jobs (int opcional), timeout (int segundos opcional), mem_mb (int opcional)
    Retorna: PDF OCR inline
    """
    f = request.files.get("file")
    if not f or not f.filename:
        raise BadRequest("Envie um PDF em 'file'.")

    lang   = (request.form.get("lang") or "").strip() or None
    force  = _b(request.form.get("force"), False)
    skip_t = _b(request.form.get("skip_text"), True)
    deskew = _b(request.form.get("deskew"), True)
    rotate = _b(request.form.get("rotate_pages"), True)
    clean  = _b(request.form.get("clean"), True)

    try:
        optimize = int(request.form.get("optimize") or 2)
    except Exception:
        optimize = 2

    try:
        jobs    = request.form.get("jobs");    jobs    = int(jobs) if jobs is not None else None
        timeout = request.form.get("timeout"); timeout = int(timeout) if timeout is not None else None
        mem_mb  = request.form.get("mem_mb");  mem_mb  = int(mem_mb) if mem_mb is not None else None
    except Exception:
        raise BadRequest("Parâmetros numéricos inválidos (jobs/timeout/mem_mb).")

    out_path = ocr_upload_file(
        f, lang=lang, force=force, skip_text=skip_t, optimize=optimize,
        deskew=deskew, rotate_pages=rotate, clean=clean,
        jobs=jobs, timeout=timeout, mem_mb=mem_mb,
    )

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
        download_name="ocr.pdf",
        conditional=True,
        max_age=0
    )

@ocr_bp.get("/options")
def ocr_options():
    """Retorna defaults úteis para o front exibir."""
    return jsonify({
        "defaults": {
            "lang": "por+eng",
            "force": False,
            "skip_text": True,
            "optimize": 2,
            "deskew": True,
            "rotate_pages": True,
            "clean": True,
            "jobs": 1,
            "timeout": 300,
            "mem_mb": 1024
        }
    })