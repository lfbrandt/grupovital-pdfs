# app/routes/preview.py
import os
import re
import uuid
from typing import Any, Optional

from flask import Blueprint, request, jsonify, send_file, current_app, abort
from werkzeug.exceptions import BadRequest

from .. import limiter
from ..utils.config_utils import validate_upload, sanitize_filename
from ..utils.preview_utils import preview_pdf, THUMBS_SUBDIR

preview_bp = Blueprint("preview", __name__, url_prefix="/api/preview")
SAFE_NAME_RE = re.compile(r"^[a-f0-9]{16,64}$")


def _tmp_previews_dir() -> str:
    base = current_app.config.get("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
    tmp = os.path.join(base, "tmp_previews")
    os.makedirs(tmp, exist_ok=True)
    return tmp


def _basename_no_ext(path: str) -> str:
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name


def _extract_thumb_id(res: Any) -> Optional[str]:
    if isinstance(res, dict) and "thumb_id" in res:
        return str(res["thumb_id"])
    if isinstance(res, dict) and isinstance(res.get("thumbnails"), list) and res["thumbnails"]:
        first = res["thumbnails"][0]
        if isinstance(first, dict):
            return str(first.get("id") or first.get("thumb_id") or _basename_no_ext(first.get("path", "") or first.get("file", "") or ""))
        if isinstance(first, str):
            return _basename_no_ext(first)
    if isinstance(res, (list, tuple)) and res:
        first = res[0]
        if isinstance(first, dict):
            return str(first.get("id") or first.get("thumb_id") or _basename_no_ext(first.get("path", "") or first.get("file", "") or ""))
        if isinstance(first, str):
            return _basename_no_ext(first)
    return None


def _validate_pdf_upload(file_storage):
    """Compatível com versões antigas/novas de validate_upload + fallback local."""
    try:
        validate_upload(file_storage, allowed_exts={"pdf"}, allowed_mimes={"application/pdf"})  # type: ignore[arg-type]
        return
    except TypeError:
        pass
    except BadRequest:
        raise
    try:
        validate_upload(file_storage, {"pdf"}, {"application/pdf"})  # type: ignore[misc]
        return
    except TypeError:
        pass
    except BadRequest:
        raise
    try:
        validate_upload(file_storage)
    except TypeError:
        pass  # segue para validação local

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


@preview_bp.route("", methods=["POST"])
@preview_bp.route("/", methods=["POST"])
@limiter.limit("20 per minute")
def create_preview():
    """
    Recebe um PDF (campo 'file') e retorna:
      { "thumb_url": "/api/preview/<thumb_id>.png", "thumb_id": "<hash>" }
    """
    file = request.files.get("file")
    if not file:
        raise BadRequest("Arquivo não enviado (campo 'file').")

    _validate_pdf_upload(file)

    tmp_dir = _tmp_previews_dir()
    safe_name = sanitize_filename(file.filename or f"{uuid.uuid4()}.pdf")
    tmp_path = os.path.join(tmp_dir, safe_name)
    file.save(tmp_path)

    try:
        result = preview_pdf(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    thumb_id = _extract_thumb_id(result)
    if not thumb_id:
        raise BadRequest("Falha ao gerar miniatura.")
    if not SAFE_NAME_RE.match(thumb_id):
        raise BadRequest("Identificador de miniatura inválido.")

    return jsonify({
        "thumb_url": f"/api/preview/{thumb_id}.png",
        "thumb_id": thumb_id
    })


@preview_bp.route("/<thumb_id>.png", methods=["GET"])
@limiter.limit("60 per minute")
def get_preview(thumb_id: str):
    """Serve a miniatura do cache com cache público por 1 dia."""
    if not SAFE_NAME_RE.match(thumb_id):
        abort(400)

    base = current_app.config.get("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
    path = os.path.join(base, THUMBS_SUBDIR, f"{thumb_id}.png")
    if not os.path.exists(path):
        abort(404)

    resp = send_file(path, mimetype="image/png", conditional=True, max_age=86400)
    resp.cache_control.public = True
    resp.cache_control.max_age = 86400
    try:
        resp.cache_control.immutable = True
    except Exception:
        pass
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = f'inline; filename="{thumb_id}.png"'
    return resp