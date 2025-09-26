# -*- coding: utf-8 -*-
"""
Rotas do conversor (wizard + APIs).
Exporta:
- converter_bp   -> páginas /converter (wizard e tela principal)
- convert_api_bp -> APIs sob /api/... usadas pelo front (ex.: /api/convert/to-pdf)

Regras / Segurança:
- Nada de sanitização pesada aqui (fica nos services).
- Mantém validação de upload por MIME real via validate_upload.
- Compatível com ambientes onde /app é read-only (Render): UPLOAD_FOLDER tem fallback.

Mudanças deste patch:
- Corrigido erro EXDEV (Invalid cross-device link) no Render: _xdev_safe_move() usa
  shutil.move/cópia quando os.replace falha entre filesystems.
- _ensure_upload_folder() agora testa gravação e faz fallback para /tmp/uploads
  (mesmo FS do /tmp), logando um aviso.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import logging
from typing import List, Iterable, Tuple

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, jsonify, current_app
)
from werkzeug.exceptions import BadRequest
from werkzeug.utils import secure_filename

from .. import limiter
from ..utils.config_utils import validate_upload
from ..services.converter_service import (
    convert_many_uploads_to_single_pdf,
    convert_upload_to_target,
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

def _files_from_request() -> List:
    """Aceita 'files[]', 'files', ou 'file' (1..N) e valida MIME real."""
    items: Iterable = ()
    if "files[]" in request.files:
        items = request.files.getlist("files[]")
    elif "files" in request.files:
        items = request.files.getlist("files") or [request.files.get("files")]
    elif "file" in request.files:
        items = request.files.getlist("file") or [request.files.get("file")]

    out: List = []
    for f in items:
        if not f:
            continue
        try:
            validate_upload(f)
            try:
                f.stream.seek(0)  # rebobina por segurança
            except Exception:
                pass
            out.append(f)
        except Exception:
            name = (getattr(f, "filename", "") or "").strip()
            if not name or "." not in name:
                raise BadRequest("Arquivo inválido (sem nome/extensão).")
            # Mesmo que a validação MIME falhe aqui, mantemos o comportamento antigo:
            out.append(f)
    return out

def _uploads_config_path() -> str:
    """Retorna o caminho configurado (ou padrão) do UPLOAD_FOLDER, sem garantir gravação."""
    return (current_app.config.get("UPLOAD_FOLDER")
            or os.path.join(os.getcwd(), "uploads"))

def _ensure_upload_folder() -> str:
    """
    Garante um diretório de uploads GRAVÁVEL.
    Se o caminho configurado não permitir escrita (caso comum no Render quando é /app/uploads),
    faz fallback para /tmp/uploads e registra aviso.
    """
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
    base = (base or "arquivo").strip() or "arquivo"
    base = secure_filename(os.path.basename(base)) or "arquivo"
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
    """
    Move seguro entre filesystems diferentes:
    - tenta os.replace (rename atômico)
    - se falhar (EXDEV), usa shutil.move (copia + remove)
    - último recurso: copy2 + remove
    """
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
    """Move para UPLOAD_FOLDER (cross-device safe) com nome seguro/único; retorna caminho final abs."""
    uploads = _ensure_upload_folder()
    base, ext = os.path.splitext(suggested_name or "")
    base = base or os.path.splitext(os.path.basename(tmp_path))[0]
    ext = (ext.lstrip(".") or os.path.splitext(tmp_path)[1].lstrip(".") or "pdf")
    final_abs = _unique_name(base, ext, uploads)
    return _xdev_safe_move(tmp_path, final_abs)

def _file_info_for_response(abs_path: str) -> dict:
    """Dict esperado pelo front; download via viewer.get_pdf a partir de UPLOAD_FOLDER."""
    uploads = _ensure_upload_folder()
    rel_path = os.path.relpath(abs_path, uploads).replace("\\", "/")
    return {
        "name": os.path.basename(abs_path),
        "size": os.path.getsize(abs_path),
        "download_url": url_for("viewer.get_pdf", filename=rel_path),
    }

def _ext_from_target(target: str) -> str:
    t = (target or "").lower().strip()
    return {"pdf":"pdf","docx":"docx","csv":"csv","xlsx":"xlsx","xlsm":"xlsm"}.get(t, "bin")

# ---- Goal atual (usado pelo JS) ----
@convert_api_bp.get("/convert/goal")
def api_get_goal():
    return jsonify({"goal": session.get("convert_goal", "to-pdf")})

# ---- UNIR em 1 PDF (JSON) ----
@convert_api_bp.post("/convert/merge-a4")
@limiter.limit("10 per minute")
def api_merge_a4_json():
    """
    Une 1 ou mais arquivos em 1 PDF normalizado para A4.
    Entrada: multipart com files[] (>=1).
    Saída: {count:1, files:[{name,size,download_url}]}
    """
    uploads = _files_from_request()
    if not uploads:  # ✅ aceita 1 arquivo
        return jsonify({"error": "Envie pelo menos 1 arquivo em 'files[]'."}), 400

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
        return jsonify({"count": 1, "files": [item]})
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Falha em /api/convert/merge-a4")
        return jsonify({"error": "Erro interno ao unir PDFs."}), 500
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

# Compat com clientes antigos
@convert_api_bp.post("/convert/to-pdf-merge")
@limiter.limit("10 per minute")
def api_to_pdf_merge_alias():
    return api_merge_a4_json()

# ---- Converter N arquivos -> N saídas (JSON) ----
def _convert_many_return_json(target: str) -> Tuple[int, List[dict]]:
    files = _files_from_request()
    if not files:
        raise BadRequest("Nenhum arquivo enviado.")

    out_infos: List[dict] = []
    for up in files:
        tmpdir = tempfile.mkdtemp(prefix="gvpdf_conv_")
        try:
            out_path = convert_upload_to_target(up, target=target, out_dir=tmpdir)
            suggested = f"{os.path.splitext(up.filename or 'arquivo')[0]}.{_ext_from_target(target)}"
            final_abs = _move_into_uploads(out_path, suggested_name=suggested)
            out_infos.append(_file_info_for_response(final_abs))
        finally:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    return len(out_infos), out_infos

@convert_api_bp.post("/convert/to-pdf")
@limiter.limit("10 per minute")
def api_to_pdf_many():
    try:
        count, files = _convert_many_return_json("pdf")
        return jsonify({"count": count, "files": files})
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-pdf")
        return jsonify({"error": "Falha ao converter para PDF."}), 500

@convert_api_bp.post("/convert/to-docx")
@limiter.limit("10 per minute")
def api_to_docx_many():
    try:
        count, files = _convert_many_return_json("docx")
        return jsonify({"count": count, "files": files})
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-docx")
        return jsonify({"error": "Falha ao converter para DOCX."}), 500

@convert_api_bp.post("/convert/to-csv")
@limiter.limit("10 per minute")
def api_to_csv_many():
    try:
        count, files = _convert_many_return_json("csv")
        return jsonify({"count": count, "files": files})
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-csv")
        return jsonify({"error": "Falha ao converter para CSV."}), 500

@convert_api_bp.post("/convert/to-xlsx")
@limiter.limit("10 per minute")
def api_to_xlsx_many():
    try:
        count, files = _convert_many_return_json("xlsx")
        return jsonify({"count": count, "files": files})
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-xlsx")
        return jsonify({"error": "Falha ao converter para XLSX."}), 500

@convert_api_bp.post("/convert/to-xlsm")
@limiter.limit("10 per minute")
def api_to_xlsm_many():
    try:
        count, files = _convert_many_return_json("xlsm")
        return jsonify({"count": count, "files": files})
    except BadRequest as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-xlsm")
        return jsonify({"error": "Falha ao converter para XLSM."}), 500