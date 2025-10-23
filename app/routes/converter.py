# app/routes/converter.py
# -*- coding: utf-8 -*-
"""
Rotas do conversor (wizard + APIs).
Exporta:
- converter_bp   -> páginas /converter (wizard e tela principal)
- convert_api_bp -> APIs sob /api/... usadas pelo front

Regras / Segurança:
- Validação de upload por extensão (e MIME real, quando suportado por validate_upload).
- Compat com ambientes read-only: UPLOAD_FOLDER tem fallback gravável (/tmp/uploads).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import logging
import subprocess
from typing import List, Iterable, Tuple, Optional
from inspect import signature

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, jsonify, current_app
)
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .. import limiter
from ..utils.config_utils import validate_upload
from ..utils.stats import record_job_event  # métricas 7.1
from ..services.converter_service import (
    convert_many_uploads_to_single_pdf,
    convert_upload_to_target,
    IMG_EXTS, DOC_EXTS, SHEET_EXTS,   # usados para whitelist
)

logger = logging.getLogger(__name__)

# ----------------- PÁGINAS -----------------
converter_bp = Blueprint("converter", __name__, url_prefix="/converter")

VALID_GOALS = {
    "to-pdf", "pdf-to-docx", "pdf-to-xlsx", "pdf-to-csv",
    "sheet-to-csv", "sheet-to-xlsm",
}

@converter_bp.get("/select")
def converter_select_page():
    return render_template("convert_wizard.html")

@converter_bp.get("/set/<goal>")
def set_convert_goal(goal: str):
    g = (goal or "").strip().lower()
    if g not in VALID_GOALS:
        raise BadRequest("Objetivo de conversão inválido.")
    session["convert_goal"] = g
    return redirect(url_for("converter.converter_page"))

@converter_bp.get("/")
def converter_page():
    goal = session.get("convert_goal", "to-pdf")
    return render_template("converter.html", goal=goal)

# ----------------- API -----------------
convert_api_bp = Blueprint("convert_api", __name__, url_prefix="/api")

# Aliases de destino (compat front antigo)
_TARGET_ALIASES = {
    "pdf": "pdf",
    "docx": "docx", "doc": "docx", "docs": "docx", "word": "docx",
    "xlsx": "xlsx", "excel": "xlsx",
    "xlsm": "xlsm",
    "csv": "csv",
}
def _norm_target(raw: str | None) -> str:
    t = (raw or "").strip().lower()
    if t in _TARGET_ALIASES:
        return _TARGET_ALIASES[t]
    raise BadRequest("Destino não suportado. Use pdf, docx, csv, xlsx ou xlsm.")

# ---------- Whitelists (sem ponto) ----------
ALLOWED_ANY_TO_PDF     = {"pdf"} | IMG_EXTS | DOC_EXTS | SHEET_EXTS
ALLOWED_PDF_ONLY       = {"pdf"}
ALLOWED_PDF_OR_SHEETS  = {"pdf"} | SHEET_EXTS
ALLOWED_SHEETS_ONLY    = set(SHEET_EXTS)

def _dotset(exts: Optional[set[str]]) -> Optional[set[str]]:
    if exts is None:
        return None
    return {"." + e.lstrip(".").lower() for e in exts}

# --- compat de assinatura do validate_upload ---
def _validate_file_upload(f, allowed_exts_dotset: Optional[set[str]]):
    """
    Chama validate_upload com o que a função suportar.
    Em algumas bases ela NÃO tem 'allowed_mimetypes'.
    """
    try:
        params = signature(validate_upload).parameters
        if "allowed_mimetypes" in params:
            return validate_upload(f, allowed_extensions=allowed_exts_dotset, allowed_mimetypes=None)
        else:
            return validate_upload(f, allowed_extensions=allowed_exts_dotset)
    except TypeError:
        # fallback: chama só com allowed_extensions
        return validate_upload(f, allowed_extensions=allowed_exts_dotset)

def _files_from_request(allowed_exts: Optional[set[str]] = None) -> List:
    """
    Aceita 'files[]', 'files', ou 'file' (1..N) e valida por extensão (e MIME real quando disponível).
    allowed_exts: conjunto *sem ponto* (ex.: {'pdf','docx'}). Se None, aceita tudo suportado para 'to-pdf'.
    """
    items: Iterable = ()
    if "files[]" in request.files:
        items = request.files.getlist("files[]")
    elif "files" in request.files:
        items = request.files.getlist("files") or [request.files.get("files")]
    elif "file" in request.files:
        items = request.files.getlist("file") or [request.files.get("file")]

    eff_allowed = _dotset(allowed_exts or ALLOWED_ANY_TO_PDF)

    out: List = []
    for f in items:
        if not f:
            continue
        _validate_file_upload(f, eff_allowed)
        try:
            f.stream.seek(0)  # rebobina por segurança
        except Exception:
            pass
        out.append(f)
    if not out:
        raise BadRequest("Nenhum arquivo válido enviado.")
    return out

def _uploads_config_path() -> str:
    return (current_app.config.get("UPLOAD_FOLDER") or os.path.join(os.getcwd(), "uploads"))

def _ensure_upload_folder() -> str:
    cfg_dir = os.path.abspath(_uploads_config_path())
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        test_path = os.path.join(cfg_dir, ".wtest")
        with open(test_path, "wb") as fh:
            fh.write(b"x")
        os.remove(test_path)
        return cfg_dir
    except Exception:
        tmp_dir = "/tmp/uploads"
        os.makedirs(tmp_dir, exist_ok=True)
        current_app.logger.warning("UPLOAD_FOLDER '%s' não é gravável; usando fallback %s", cfg_dir, tmp_dir)
        return tmp_dir

def _unique_name(base: str, ext: str, folder: str) -> str:
    base = (secure_filename(os.path.basename(base or "arquivo")) or "arquivo")
    ext = (ext or "").lstrip(".") or "pdf"
    name = f"{base}.{ext}"
    i = 1
    abs_path = os.path.join(folder, name)
    while os.path.exists(abs_path):
        name = f"{base} ({i}).{ext}"
        abs_path = os.path.join(folder, name)
        i += 1
    return abs_path

def _xdev_safe_move(src: str, dst: str) -> str:
    if not src or not os.path.exists(src):
        raise BadRequest("Arquivo temporário inexistente.")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.replace(src, dst)
        return dst
    except OSError:
        try:
            shutil.move(src, dst)
            return dst
        except Exception:
            shutil.copy2(src, dst)
            try:
                os.remove(src)
            except Exception:
                pass
            return dst

def _move_into_uploads(tmp_path: str, suggested_name: str) -> str:
    uploads = _ensure_upload_folder()
    base, ext = os.path.splitext(suggested_name or "")
    base = base or os.path.splitext(os.path.basename(tmp_path))[0]
    ext = (ext.lstrip(".") or os.path.splitext(tmp_path)[1].lstrip(".") or "pdf")
    final_abs = _unique_name(base, ext, uploads)
    return _xdev_safe_move(tmp_path, final_abs)

def _file_info_for_response(abs_path: str) -> dict:
    uploads = _ensure_upload_folder()
    rel_path = os.path.relpath(abs_path, uploads).replace("\\", "/")
    return {
        "name": os.path.basename(abs_path),
        "size": os.path.getsize(abs_path),
        "download_url": url_for("viewer.get_pdf", filename=rel_path),
    }

def _ext_from_target(target: str) -> str:
    t = (target or "").lower().strip()
    return {"pdf": "pdf", "docx": "docx", "csv": "csv", "xlsx": "xlsx", "xlsm": "xlsm"}.get(t, "bin")

# ---------- Aux JSON ----------
@convert_api_bp.get("/convert/goal")
def api_get_goal():
    return jsonify({"goal": session.get("convert_goal", "to-pdf")})

# Healthcheck simples do LibreOffice (útil no host Linux/Render)
@convert_api_bp.get("/convert/health")
def api_convert_health():
    try:
        out = subprocess.run(["soffice", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
        return jsonify({"ok": True, "lo": out.stdout.strip()})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "LibreOffice (soffice) não encontrado no PATH."}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"Falha ao executar soffice: {e}"}), 500

# ---------- Unir em 1 PDF ----------
@convert_api_bp.post("/convert/merge-a4")
@limiter.limit("10 per minute")
def api_merge_a4_json():
    try:
        uploads = _files_from_request(ALLOWED_ANY_TO_PDF)
        normalize_str = request.form.get("normalize", "on")
        if isinstance(normalize_str, bool):
            normalize_str = "on" if normalize_str else "off"
        else:
            normalize_str = (str(normalize_str or "on").strip().lower() or "on")
        norm_page_size = request.form.get("norm_page_size", "A4")

        tmpdir = tempfile.mkdtemp(prefix="gvpdf_merge_")
        try:
            final_pdf = convert_many_uploads_to_single_pdf(
                uploads=uploads,
                workdir=tmpdir,
                normalize=normalize_str,
                norm_page_size=norm_page_size,
            )
            final_abs = _move_into_uploads(final_pdf, suggested_name="arquivos_unidos.pdf")
            item = _file_info_for_response(final_abs)

            # métricas
            try:
                bytes_out = item.get("size")
                bytes_in = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert/merge-a4", action="convert-merge", bytes_in=bytes_in, bytes_out=bytes_out, files_out=1)
            except Exception:
                pass

            return jsonify({"count": 1, "files": [item]})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Falha em /api/convert/merge-a4")
        return jsonify({"error": "Erro interno ao unir PDFs."}), 500

@convert_api_bp.post("/convert/to-pdf-merge")
@limiter.limit("10 per minute")
def api_to_pdf_merge_alias():
    return api_merge_a4_json()

# ---------- Conversões 1->1 (N arquivos) ----------
def _convert_many_return_json(target: str, allowed_exts: Optional[set[str]]) -> Tuple[int, List[dict]]:
    files = _files_from_request(allowed_exts)
    out_infos: List[dict] = []
    for up in files:
        tmpdir = tempfile.mkdtemp(prefix="gvpdf_conv_")
        try:
            out_path = convert_upload_to_target(up, target=target, out_dir=tmpdir)
            suggested = f"{os.path.splitext(up.filename or 'arquivo')[0]}.{_ext_from_target(target)}"
            final_abs = _move_into_uploads(out_path, suggested_name=suggested)
            out_infos.append(_file_info_for_response(final_abs))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return len(out_infos), out_infos

@convert_api_bp.post("/convert/to-pdf")
@limiter.limit("10 per minute")
def api_to_pdf_many():
    try:
        count, files = _convert_many_return_json("pdf", ALLOWED_ANY_TO_PDF)

        # métricas
        try:
            bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
            bytes_in  = int(request.content_length) if request.content_length else None
            record_job_event(route="/api/convert/to-pdf", action="to-pdf", bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
        except Exception:
            pass

        return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-pdf")
        return jsonify({"error": "Falha ao converter para PDF."}), 500

@convert_api_bp.post("/convert/to-docx")
@limiter.limit("10 per minute")
def api_to_docx_many():
    try:
        # PDF → DOCX
        count, files = _convert_many_return_json("docx", ALLOWED_PDF_ONLY)

        try:
            bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
            bytes_in  = int(request.content_length) if request.content_length else None
            record_job_event(route="/api/convert/to-docx", action="to-docx", bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
        except Exception:
            pass

        return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-docx")
        return jsonify({"error": "Falha ao converter para DOCX."}), 500

@convert_api_bp.post("/convert/to-csv")
@limiter.limit("10 per minute")
def api_to_csv_many():
    try:
        # PDF/Planilhas → CSV
        count, files = _convert_many_return_json("csv", ALLOWED_PDF_OR_SHEETS)

        try:
            bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
            bytes_in  = int(request.content_length) if request.content_length else None
            record_job_event(route="/api/convert/to-csv", action="to-csv", bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
        except Exception:
            pass

        return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-csv")
        return jsonify({"error": "Falha ao converter para CSV."}), 500

@convert_api_bp.post("/convert/to-xlsx")
@limiter.limit("10 per minute")
def api_to_xlsx_many():
    try:
        # PDF/Planilhas → XLSX
        count, files = _convert_many_return_json("xlsx", ALLOWED_PDF_OR_SHEETS)

        try:
            bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
            bytes_in  = int(request.content_length) if request.content_length else None
            record_job_event(route="/api/convert/to-xlsx", action="to-xlsx", bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
        except Exception:
            pass

        return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-xlsx")
        return jsonify({"error": "Falha ao converter para XLSX."}), 500

@convert_api_bp.post("/convert/to-xlsm")
@limiter.limit("10 per minute")
def api_to_xlsm_many():
    try:
        # Apenas planilhas → XLSM
        count, files = _convert_many_return_json("xlsm", ALLOWED_SHEETS_ONLY)

        try:
            bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
            bytes_in  = int(request.content_length) if request.content_length else None
            record_job_event(route="/api/convert/to-xlsm", action="to-xlsm", bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
        except Exception:
            pass

        return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-xlsm")
        return jsonify({"error": "Falha ao converter para XLSM."}), 500

# ---------- Endpoint genérico -----------
@convert_api_bp.post("/convert")
@limiter.limit("10 per minute")
def api_convert_generic():
    try:
        target = _norm_target(request.form.get("target") or request.form.get("to"))

        if target == "pdf":
            allow = ALLOWED_ANY_TO_PDF
        elif target == "docx":
            allow = ALLOWED_PDF_ONLY
        elif target in {"csv", "xlsx"}:
            allow = ALLOWED_PDF_OR_SHEETS
        elif target == "xlsm":
            allow = ALLOWED_SHEETS_ONLY
        else:
            allow = ALLOWED_ANY_TO_PDF

        count, files = _convert_many_return_json(target, allow)

        try:
            bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
            bytes_in  = int(request.content_length) if request.content_length else None
            record_job_event(route="/api/convert", action=f"to-{target}", bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
        except Exception:
            pass

        return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except Exception:
        current_app.logger.exception("Erro em /api/convert (genérico)")
        return jsonify({"error": "Falha ao converter arquivo(s)."}), 500

# ---- handlers JSON para 429 (limiter) ---
@convert_api_bp.errorhandler(429)
def handle_429(e):
    return jsonify({"error": "Muitas requisições. Tente novamente em instantes."}), 429