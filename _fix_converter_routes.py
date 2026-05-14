"""
Reconstrói a seção de rotas 1->1 do converter.py de forma limpa.
Mantém tudo até a linha do comentário '# ---------- Conversões 1->1'
e reescreve do zero a partir daí.
"""
import re

SRC = "app/routes/converter.py"

with open(SRC, encoding="utf-8") as f:
    content = f.read()

# Ponto de corte: antes da definição de _convert_many_return_json
CUTMARK = "\n# ---------- Conversões 1->1 (N arquivos) ----------"
idx = content.find(CUTMARK)
if idx == -1:
    raise RuntimeError("Ponto de corte não encontrado!")

header = content[:idx]

tail = '''
# ---------- Conversões 1->1 (N arquivos) ----------
def _convert_many_return_json(target: str, allowed_exts: Optional[set[str]]) -> Tuple[int, List[dict]]:
    files = _files_from_request(allowed_exts)
    out_infos: List[dict] = []
    for up in files:
        tmpdir = tempfile.mkdtemp(prefix="gvpdf_conv_")
        try:
            out_path = convert_upload_to_target(up, target=target, out_dir=tmpdir)
            suggested = f"{os.path.splitext(up.filename or \'arquivo\')[0]}.{_ext_from_target(target)}"
            final_abs = _move_into_uploads(out_path, suggested_name=suggested)
            out_infos.append(_file_info_for_response(final_abs))
        except RuntimeError:
            raise  # re-levanta para a rota capturar como 503
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return len(out_infos), out_infos


def _route_error_handlers(route_name: str, friendly_msg: str):
    """Retorna os blocos except padronizados — usado apenas como doc."""


@convert_api_bp.post("/convert/to-pdf")
@limiter.limit("10 per minute")
def api_to_pdf_many():
    try:
        count, files = _convert_many_return_json("pdf", ALLOWED_ANY_TO_PDF)
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
        current_app.logger.warning("Erro em /api/convert/to-pdf (runtime): %s", e)
        return jsonify({"error": str(e)}), 503
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-pdf")
        return jsonify({"error": "Falha ao converter para PDF."}), 500


@convert_api_bp.post("/convert/to-docx")
@limiter.limit("10 per minute")
def api_to_docx_many():
    try:
        count, files = _convert_many_return_json("docx", ALLOWED_PDF_ONLY)
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
        current_app.logger.warning("Erro em /api/convert/to-docx (runtime): %s", e)
        return jsonify({"error": str(e)}), 503
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-docx")
        return jsonify({"error": "Falha ao converter para DOCX."}), 500


@convert_api_bp.post("/convert/to-csv")
@limiter.limit("10 per minute")
def api_to_csv_many():
    try:
        count, files = _convert_many_return_json("csv", ALLOWED_PDF_OR_SHEETS)
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
        current_app.logger.warning("Erro em /api/convert/to-csv (runtime): %s", e)
        return jsonify({"error": str(e)}), 503
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-csv")
        return jsonify({"error": "Falha ao converter para CSV."}), 500


@convert_api_bp.post("/convert/to-xlsx")
@limiter.limit("10 per minute")
def api_to_xlsx_many():
    try:
        count, files = _convert_many_return_json("xlsx", ALLOWED_PDF_OR_SHEETS)
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
        current_app.logger.warning("Erro em /api/convert/to-xlsx (runtime): %s", e)
        return jsonify({"error": str(e)}), 503
    except Exception:
        current_app.logger.exception("Erro em /api/convert/to-xlsx")
        return jsonify({"error": "Falha ao converter para XLSX."}), 500


@convert_api_bp.post("/convert/to-xlsm")
@limiter.limit("10 per minute")
def api_to_xlsm_many():
    try:
        count, files = _convert_many_return_json("xlsm", ALLOWED_SHEETS_ONLY)
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
        current_app.logger.warning("Erro em /api/convert/to-xlsm (runtime): %s", e)
        return jsonify({"error": str(e)}), 503
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
        current_app.logger.warning("Erro em /api/convert genérico (runtime): %s", e)
        return jsonify({"error": str(e)}), 503
    except Exception:
        current_app.logger.exception("Erro em /api/convert (genérico)")
        return jsonify({"error": "Falha ao converter arquivo(s)."}), 500


# ---- handlers JSON para 429 (limiter) ---
@convert_api_bp.errorhandler(429)
def handle_429(e):
    return jsonify({"error": "Muitas requisições. Tente novamente em instantes."}), 429
'''

result = header + tail
with open(SRC, "w", encoding="utf-8") as f:
    f.write(result)

# Verify syntax
import ast
try:
    ast.parse(result)
    print("[OK] converter.py reescrito e validado com sucesso.")
except SyntaxError as e:
    print(f"[FAIL] SyntaxError: {e}")
