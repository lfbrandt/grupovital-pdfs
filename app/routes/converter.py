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
from contextlib import contextmanager
from typing import Iterator, List, Iterable, Tuple, Optional, Sequence
from inspect import signature

from flask import (
    Blueprint, render_template, session, redirect, url_for,
    request, jsonify, current_app
)
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from .. import limiter
from ..utils.config_utils import validate_upload
from ..utils.limits import (
    get_converter_max_files,
    get_converter_max_runtime_sec,
)
from ..utils.security import (
    make_session_output_dir,
    session_owned_generated_rel_path,
)
from ..utils.stats import record_job_event  # métricas 7.1
from ..services.converter_service import (
    ConverterExtractionError,
    ConverterNoTableError,
    ConverterTimeoutError,
    ConverterToolExecutionError,
    ConverterToolUnavailableError,
    converter_job_runtime,
    convert_many_uploads_to_single_pdf,
    convert_upload_to_target,
    libreoffice_healthcheck,
    IMG_EXTS, DOC_EXTS, SHEET_EXTS,   # usados para whitelist
)
from ..services.converter_output_validation import (
    ConverterOutputValidationError,
    validate_converter_output,
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

@converter_bp.get("")
@converter_bp.get("/")
def converter_page():
    goal = session.get("convert_goal", "to-pdf")
    return render_template(
        "converter.html",
        goal=goal,
        converter_max_files=_converter_max_files(),
    )

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


def _converter_max_files() -> int:
    raw_value = current_app.config.get("CONVERTER_MAX_FILES")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else get_converter_max_files()


def _converter_max_files_message(max_files: int) -> str:
    noun = "arquivo" if max_files == 1 else "arquivos"
    return f"Envie no máximo {max_files} {noun} por vez."


def _converter_max_runtime_sec() -> int:
    raw_value = current_app.config.get("CONVERTER_MAX_RUNTIME_SEC")
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else get_converter_max_runtime_sec()


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

    valid_items = [
        f for f in items
        if f is not None and bool(getattr(f, "filename", "") or "")
    ]
    max_files = _converter_max_files()
    if len(valid_items) > max_files:
        raise BadRequest(_converter_max_files_message(max_files))

    eff_allowed = _dotset(allowed_exts or ALLOWED_ANY_TO_PDF)

    out: List = []
    for f in valid_items:
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
    test_path = os.path.join(cfg_dir, ".wtest")
    try:
        os.makedirs(cfg_dir, exist_ok=True)
        with open(test_path, "wb") as fh:
            fh.write(b"x")
        os.remove(test_path)
        return cfg_dir
    except Exception:
        tmp_dir = "/tmp/uploads"
        os.makedirs(tmp_dir, exist_ok=True)
        current_app.logger.warning("[converter] upload_folder indisponivel; usando fallback temporario")
        return tmp_dir
    finally:
        try:
            if os.path.isfile(test_path):
                os.remove(test_path)
        except OSError:
            pass

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

def _move_into_uploads(
    tmp_path: str,
    suggested_name: str,
    *,
    output_dir: Optional[str] = None,
) -> str:
    uploads = _ensure_upload_folder()
    destination_dir = output_dir or make_session_output_dir(uploads)
    uploads_real = os.path.realpath(os.path.abspath(uploads))
    destination_real = os.path.realpath(os.path.abspath(destination_dir))
    try:
        is_contained = (
            destination_real != uploads_real
            and os.path.commonpath([uploads_real, destination_real]) == uploads_real
        )
    except ValueError:
        is_contained = False
    if not is_contained or not os.path.isdir(destination_real):
        raise RuntimeError("Diretório de publicação inválido.")

    base, ext = os.path.splitext(suggested_name or "")
    base = base or os.path.splitext(os.path.basename(tmp_path))[0]
    ext = (ext.lstrip(".") or os.path.splitext(tmp_path)[1].lstrip(".") or "pdf")
    final_abs = _unique_name(base, ext, destination_real)
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


def _cleanup_published_job(output_dir: Optional[str]) -> None:
    if not output_dir:
        return

    uploads = _ensure_upload_folder()
    uploads_real = os.path.realpath(os.path.abspath(uploads))
    job_real = os.path.realpath(os.path.abspath(output_dir))
    try:
        rel_dir = os.path.relpath(job_real, uploads_real).replace("\\", "/")
    except ValueError:
        return

    ownership_probe = f"{rel_dir}/__job_cleanup__"
    if session_owned_generated_rel_path(ownership_probe) != ownership_probe:
        return

    try:
        entries = list(os.scandir(job_real))
    except OSError:
        entries = []

    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False) or entry.is_symlink():
                os.remove(entry.path)
        except OSError:
            pass

    try:
        os.rmdir(job_real)
    except OSError:
        pass


@contextmanager
def _publish_staged_outputs(
    staged_outputs: Sequence[Tuple[str, str]],
    *,
    check_deadline,
) -> Iterator[List[dict]]:
    if not staged_outputs:
        raise RuntimeError("Nenhuma saída válida foi produzida.")

    uploads = _ensure_upload_folder()
    output_dir = make_session_output_dir(uploads)
    published_paths: List[str] = []
    try:
        for staged_path, suggested_name in staged_outputs:
            check_deadline()
            published_paths.append(
                _move_into_uploads(
                    staged_path,
                    suggested_name=suggested_name,
                    output_dir=output_dir,
                )
            )

        infos = []
        for path in published_paths:
            check_deadline()
            infos.append(_file_info_for_response(path))
        check_deadline()
        yield infos
        check_deadline()
    except BaseException:
        _cleanup_published_job(output_dir)
        raise


def _log_converter_controlled(stage: str, exc: BaseException, *, level: str = "warning") -> None:
    log = current_app.logger.warning if level == "warning" else current_app.logger.error
    log("[converter] %s falhou: %s", stage, type(exc).__name__)


def _runtime_error_response(
    stage: str,
    exc: RuntimeError,
    fallback_message: str,
):
    _log_converter_controlled(stage, exc)
    if isinstance(exc, ConverterNoTableError):
        return jsonify({
            "error": "Nenhuma tabela utilizável foi encontrada no PDF."
        }), 422
    if isinstance(exc, ConverterExtractionError):
        return jsonify({
            "error": "Não foi possível extrair uma tabela válida do PDF."
        }), 503
    if isinstance(exc, ConverterTimeoutError):
        return jsonify({
            "error": (
                "A conversão excedeu o tempo máximo permitido. "
                "Tente novamente com menos arquivos."
            )
        }), 503
    if isinstance(exc, ConverterToolUnavailableError):
        return jsonify({
            "error": (
                "A ferramenta necessária para esta conversão "
                "não está disponível."
            )
        }), 503
    if isinstance(exc, ConverterOutputValidationError):
        return jsonify({
            "error": (
                "A conversão não gerou um arquivo válido para download."
            )
        }), 503
    if isinstance(exc, ConverterToolExecutionError):
        return jsonify({
            "error": (
                "A ferramenta de conversão não concluiu o processamento."
            )
        }), 503
    return jsonify({"error": fallback_message}), 503


# ---------- Aux JSON ----------
@convert_api_bp.get("/convert/goal")
def api_get_goal():
    return jsonify({"goal": session.get("convert_goal", "to-pdf")})

# Healthcheck simples do LibreOffice (útil no host Linux/Render)
@convert_api_bp.get("/convert/health")
def api_convert_health():
    try:
        return jsonify({"ok": True, "lo": libreoffice_healthcheck(timeout=5)})
    except ConverterToolUnavailableError as exc:
        _log_converter_controlled("health-unavailable", exc)
        return jsonify({
            "ok": False,
            "error": "LibreOffice não está disponível.",
        }), 503
    except ConverterTimeoutError as exc:
        _log_converter_controlled("health-timeout", exc)
        return jsonify({
            "ok": False,
            "error": "O healthcheck do LibreOffice excedeu o tempo limite.",
        }), 503
    except ConverterToolExecutionError as exc:
        _log_converter_controlled("health-execution", exc)
        return jsonify({
            "ok": False,
            "error": "Não foi possível verificar o LibreOffice.",
        }), 503
    except Exception as exc:
        _log_converter_controlled("health", exc, level="error")
        return jsonify({
            "ok": False,
            "error": "Não foi possível verificar o LibreOffice.",
        }), 503

# ---------- Unir em 1 PDF ----------
def _merge_a4_json_response():
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
            with converter_job_runtime(
                _converter_max_runtime_sec()
            ) as runtime:
                final_pdf = convert_many_uploads_to_single_pdf(
                    uploads=uploads,
                    workdir=tmpdir,
                    normalize=normalize_str,
                    norm_page_size=norm_page_size,
                )
                runtime.remaining("merge-conversion")
                staged_pdf = validate_converter_output(
                    final_pdf,
                    tmpdir,
                    "pdf",
                    check_deadline=lambda: runtime.remaining(
                        "merge-validation"
                    ),
                )
                with _publish_staged_outputs(
                    [(staged_pdf, "arquivos_unidos.pdf")],
                    check_deadline=lambda: runtime.remaining(
                        "merge-publication"
                    ),
                ) as files:
                    item = files[0]

                    # métricas
                    try:
                        bytes_out = item.get("size")
                        bytes_in = int(request.content_length) if request.content_length else None
                        record_job_event(route="/api/convert/merge-a4", action="convert-merge", bytes_in=bytes_in, bytes_out=bytes_out, files_out=1)
                    except Exception:
                        pass
                    return jsonify({"count": 1, "files": files})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "merge-a4-runtime",
            e,
            "Não foi possível preparar os PDFs para união.",
        )
    except Exception as e:
        _log_converter_controlled("merge-a4", e, level="error")
        return jsonify({"error": "Erro interno ao unir PDFs."}), 500


@convert_api_bp.post("/convert/merge-a4")
@limiter.limit("10 per minute")
def api_merge_a4_json():
    return _merge_a4_json_response()


@convert_api_bp.post("/convert/to-pdf-merge")
@limiter.limit("10 per minute")
def api_to_pdf_merge_alias():
    return _merge_a4_json_response()

# ---------- Conversões 1->1 (N arquivos) ----------
@contextmanager
def _convert_many_return_json(
    target: str,
    allowed_exts: Optional[set[str]],
) -> Iterator[Tuple[int, List[dict]]]:
    files = _files_from_request(allowed_exts)
    batch_tmpdir = tempfile.mkdtemp(prefix="gvpdf_conv_batch_")
    raw_outputs: List[Tuple[str, str, str, str]] = []
    try:
        with converter_job_runtime(
            _converter_max_runtime_sec()
        ) as runtime:
            for index, up in enumerate(files):
                runtime.remaining("next-file")
                item_tmpdir = tempfile.mkdtemp(
                    prefix=f"gvpdf_conv_{index:04d}_",
                    dir=batch_tmpdir,
                )
                out_path = convert_upload_to_target(
                    up,
                    target=target,
                    out_dir=item_tmpdir,
                )
                runtime.remaining("file-conversion")
                suggested = (
                    f"{os.path.splitext(up.filename or 'arquivo')[0]}."
                    f"{_ext_from_target(target)}"
                )
                source_ext = os.path.splitext(
                    up.filename or ""
                )[1].lower().lstrip(".")
                raw_outputs.append(
                    (out_path, suggested, item_tmpdir, source_ext)
                )

            staged_outputs: List[Tuple[str, str]] = []
            for out_path, suggested, item_tmpdir, source_ext in raw_outputs:
                runtime.remaining("structural-validation")
                staged_outputs.append((
                    validate_converter_output(
                        out_path,
                        item_tmpdir,
                        target,
                        source_ext=source_ext,
                        require_table_data=(
                            target == "csv" and source_ext == "pdf"
                        ),
                        check_deadline=lambda: runtime.remaining(
                            "structural-validation"
                        ),
                    ),
                    suggested,
                ))

            with _publish_staged_outputs(
                staged_outputs,
                check_deadline=lambda: runtime.remaining(
                    "publication"
                ),
            ) as out_infos:
                yield len(out_infos), out_infos
    finally:
        shutil.rmtree(batch_tmpdir, ignore_errors=True)


def _route_error_handlers(route_name: str, friendly_msg: str):
    """Retorna os blocos except padronizados — usado apenas como doc."""


@convert_api_bp.post("/convert/to-pdf")
@limiter.limit("10 per minute")
def api_to_pdf_many():
    try:
        with _convert_many_return_json("pdf", ALLOWED_ANY_TO_PDF) as (count, files):
            try:
                bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
                bytes_in  = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert/to-pdf", action="to-pdf",
                                 bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
            except Exception:
                pass
            return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "to-pdf-runtime",
            e,
            "Não foi possível converter o arquivo para PDF.",
        )
    except Exception as e:
        _log_converter_controlled("to-pdf", e, level="error")
        return jsonify({"error": "Falha ao converter para PDF."}), 500


@convert_api_bp.post("/convert/to-docx")
@limiter.limit("10 per minute")
def api_to_docx_many():
    try:
        with _convert_many_return_json("docx", ALLOWED_PDF_ONLY) as (count, files):
            try:
                bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
                bytes_in  = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert/to-docx", action="to-docx",
                                 bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
            except Exception:
                pass
            return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "to-docx-runtime",
            e,
            "Não foi possível converter o arquivo para DOCX.",
        )
    except Exception as e:
        _log_converter_controlled("to-docx", e, level="error")
        return jsonify({"error": "Falha ao converter para DOCX."}), 500


@convert_api_bp.post("/convert/to-csv")
@limiter.limit("10 per minute")
def api_to_csv_many():
    try:
        with _convert_many_return_json("csv", ALLOWED_PDF_OR_SHEETS) as (count, files):
            try:
                bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
                bytes_in  = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert/to-csv", action="to-csv",
                                 bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
            except Exception:
                pass
            return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "to-csv-runtime",
            e,
            "Não foi possível converter o arquivo para CSV.",
        )
    except Exception as e:
        _log_converter_controlled("to-csv", e, level="error")
        return jsonify({"error": "Falha ao converter para CSV."}), 500


@convert_api_bp.post("/convert/to-xlsx")
@limiter.limit("10 per minute")
def api_to_xlsx_many():
    try:
        with _convert_many_return_json("xlsx", ALLOWED_PDF_OR_SHEETS) as (count, files):
            try:
                bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
                bytes_in  = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert/to-xlsx", action="to-xlsx",
                                 bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
            except Exception:
                pass
            return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "to-xlsx-runtime",
            e,
            "Não foi possível converter o arquivo para XLSX.",
        )
    except Exception as e:
        _log_converter_controlled("to-xlsx", e, level="error")
        return jsonify({"error": "Falha ao converter para XLSX."}), 500


@convert_api_bp.post("/convert/to-xlsm")
@limiter.limit("10 per minute")
def api_to_xlsm_many():
    try:
        with _convert_many_return_json("xlsm", ALLOWED_SHEETS_ONLY) as (count, files):
            try:
                bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
                bytes_in  = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert/to-xlsm", action="to-xlsm",
                                 bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
            except Exception:
                pass
            return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "to-xlsm-runtime",
            e,
            "Não foi possível converter o arquivo para XLSM.",
        )
    except Exception as e:
        _log_converter_controlled("to-xlsm", e, level="error")
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

        with _convert_many_return_json(target, allow) as (count, files):
            try:
                bytes_out = sum(int(it.get("size") or 0) for it in files) if files else None
                bytes_in  = int(request.content_length) if request.content_length else None
                record_job_event(route="/api/convert", action=f"to-{target}",
                                 bytes_in=bytes_in, bytes_out=bytes_out, files_out=count)
            except Exception:
                pass
            return jsonify({"count": count, "files": files})
    except RequestEntityTooLarge:
        return jsonify({"error": "Arquivo muito grande (MAX_CONTENT_LENGTH)."}), 413
    except BadRequest as e:
        return jsonify({"error": e.description}), 422
    except RuntimeError as e:
        return _runtime_error_response(
            "generic-runtime",
            e,
            "Não foi possível converter o arquivo.",
        )
    except Exception as e:
        _log_converter_controlled("generic", e, level="error")
        return jsonify({"error": "Falha ao converter arquivo(s)."}), 500


# ---- handlers JSON para 429 (limiter) ---
@convert_api_bp.errorhandler(429)
def handle_429(e):
    return jsonify({"error": "Muitas requisições. Tente novamente em instantes."}), 429
