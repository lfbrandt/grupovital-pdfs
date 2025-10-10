# app/routes/edit.py
# Página única do editor + APIs (/edit e /api/edit/*)

import os, uuid, shutil, json, tempfile, io, re
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, current_app, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import BadRequest, NotFound

import fitz  # PyMuPDF
try:
    import pikepdf
    from pikepdf import Name as _PdfName
except Exception:
    pikepdf = None
    _PdfName = None

# >>> NOVO: usa o serviço dedicado de OCR (com fallback interno)
try:
    from app.services.ocr_service import ocr_pdf_path as _ocr_pdf_path
    _HAS_OCR_SERVICE = True
except Exception:
    _HAS_OCR_SERVICE = False

edit_bp = Blueprint("edit_bp", __name__)

ALLOWED_MODES = ("organize", "crop", "redact", "text", "ocr", "all")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")  # defensivo

# ===== Handlers de erro (retornam JSON quando XHR/JSON) =====
def _wants_json() -> bool:
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return True
    if request.is_json:
        return True
    return False

@edit_bp.errorhandler(BadRequest)
def _handle_400(e: BadRequest):
    msg = e.description or "Requisição inválida."
    if _wants_json():
        return jsonify({"error": msg}), 400
    return msg, 400

@edit_bp.errorhandler(NotFound)
def _handle_404(e: NotFound):
    msg = e.description or "Recurso não encontrado."
    if _wants_json():
        return jsonify({"error": msg}), 404
    return msg, 404

@edit_bp.errorhandler(Exception)
def _handle_500(e: Exception):
    current_app.logger.exception("Erro não tratado em edit_bp")
    if _wants_json():
        return jsonify({"error": "Erro interno no servidor."}), 500
    return "Erro interno no servidor.", 500

# ===== MIME sniff (real) =====
def _sniff_mime_file(path: str) -> str:
    """
    Detecta o MIME real do arquivo.
    Tenta: app.utils.mime -> python-magic -> assinaturas -> imghdr.
    """
    # 1) util interno (se existir)
    try:
        from app.utils.mime import sniff_mime_type as _sniff
        m = _sniff(path)
        if m:
            return m
    except Exception:
        pass

    # 2) python-magic (em Windows: python-magic-bin)
    try:
        import magic  # type: ignore
        m = magic.from_file(path, mime=True)
        if m:
            return m
    except Exception:
        pass

    # 3) assinaturas (magic numbers) — cobre PNG/JPEG com confiabilidade
    try:
        with open(path, "rb") as f:
            sig = f.read(12)
        if sig.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if len(sig) >= 3 and sig[0:3] == b"\xFF\xD8\xFF":
            return "image/jpeg"
    except Exception:
        pass

    # 4) imghdr como último recurso
    try:
        import imghdr
        kind = imghdr.what(path)
        if kind == "png":
            return "image/png"
        if kind in ("jpeg", "jpg"):
            return "image/jpeg"
    except Exception:
        pass

    return "application/octet-stream"

# -------- helpers de sessão/arquivos --------
def _session_dir(session_id: str) -> str:
    root = os.path.join(current_app.config['UPLOAD_FOLDER'], 'edit_sessions')
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, session_id)

def _paths(session_id: str):
    sdir = _session_dir(session_id)
    return {
        "dir": sdir,
        "orig": os.path.join(sdir, "original.pdf"),
        "cur":  os.path.join(sdir, "current.pdf"),
        "meta": os.path.join(sdir, "meta.json"),
        "ovl":  os.path.join(sdir, "overlays"),
    }

def _tmp_pdf_path(session_dir: str) -> str:
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, f"tmp_{uuid.uuid4().hex[:10]}.pdf")

def _is_pdf_header(buf: bytes) -> bool:
    """True se '%PDF-' aparecer nos primeiros 1024 bytes (tolerante a BOM/ruído)."""
    if not isinstance(buf, (bytes, bytearray)):
        return False
    head = bytes(buf[:1024])
    return b"%PDF-" in head

def _ensure_pdf(path: str):
    with open(path, "rb") as f:
        head = f.read(1024)
    if not _is_pdf_header(head):
        raise BadRequest("Arquivo enviado não é um PDF válido.")

def _safe_copy_upload_to_path(up_file_storage, dest_path: str, chunk_size: int = 1024 * 1024) -> int:
    """Copia stream do upload para dest_path de forma segura. Retorna bytes gravados."""
    try:
        up_file_storage.stream.seek(0)
    except Exception:
        pass
    total = 0
    with open(dest_path, "wb") as out:
        src = up_file_storage.stream
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
    try:
        os.chmod(dest_path, 0o600)
    except Exception:
        pass
    return total

def _safe_session_id(sid: str) -> str:
    sid = (sid or "").strip()
    if not SESSION_ID_RE.match(sid):
        raise BadRequest("session_id inválido.")
    return sid

# ---------- pikepdf compat ---------- (sanitização)
def _get_pdf_root(pdf):
    if pdf is None:
        return None
    root = getattr(pdf, "Root", None)
    if root is None:
        try:
            root = pdf.trailer[_PdfName("/Root")] if _PdfName else pdf.trailer["/Root"]
        except Exception:
            root = None
    return root

def _sanitize_pdf(src: str, dst: str):
    if pikepdf is None:
        try:
            shutil.move(src, dst)
        except Exception:
            shutil.copyfile(src, dst)
        return
    try:
        with pikepdf.open(src) as pdf:
            root = _get_pdf_root(pdf)
            if root is not None:
                for key in ("/OpenAction", "/AA"):
                    try:
                        if key in root:
                            del root[key]
                    except Exception:
                        pass
                try:
                    names = root.get("/Names", None)
                    if names and "/JavaScript" in names:
                        del names["/JavaScript"]
                except Exception:
                    pass
                try:
                    acro = root.get("/AcroForm", None)
                    if acro:
                        for k in ("/XFA", "/NeedAppearances"):
                            if k in acro:
                                del acro[k]
                except Exception:
                    pass
            try:
                pdf.remove_unreferenced_resources()
            except Exception:
                pass
            pdf.save(dst, fix_metadata=True, linearize=True)
    except Exception:
        try:
            shutil.copyfile(src, dst)
        except Exception:
            raise
    finally:
        try:
            if os.path.exists(src):
                os.remove(src)
        except Exception:
            pass

def _clamp_num(v, lo, hi):
    try:
        v = float(v)
    except Exception:
        v = lo
    return max(lo, min(hi, v))

def _parse_hex_color(hexstr):
    hs = (hexstr or '#000000').lstrip('#')
    if len(hs) == 3:
        hs = ''.join(c * 2 for c in hs)
    try:
        r = int(hs[0:2], 16) / 255.0
        g = int(hs[2:4], 16) / 255.0
        b = int(hs[4:6], 16) / 255.0
        return (r, g, b)
    except Exception:
        return (0, 0, 0)

# -------- páginas --------
@edit_bp.route("/edit/options", methods=["GET"])
def legacy_options():
    return redirect(url_for("edit_bp.edit"), code=301)

@edit_bp.route("/edit/", methods=["GET"])
def edit():
    mode = (request.args.get("mode") or "organize").lower()
    if mode not in ALLOWED_MODES:
        mode = "organize"
    return render_template("edit.html", modes=ALLOWED_MODES, default_mode=mode)

# -------- APIs --------
@edit_bp.post("/api/edit/upload")
def api_edit_upload():
    # Aceita múltiplos aliases do campo
    up = (
        request.files.get("file")
        or request.files.get("pdf")
        or request.files.get("upload")
        or request.files.get("document")
    )
    if not up or not up.filename:
        current_app.logger.info("Upload falhou: nenhum arquivo recebido (campos=file|pdf|upload|document).")
        raise BadRequest("Nenhum arquivo enviado.")

    # Nome seguro com extensão .pdf (se conteúdo for PDF)
    orig_name = up.filename
    filename = secure_filename(orig_name or "upload.pdf")
    if not filename.lower().endswith(".pdf"):
        # ainda vamos permitir se o conteúdo for PDF (sniff + header)
        filename = filename + ".pdf"

    session_id = uuid.uuid4().hex[:12]
    paths = _paths(session_id)
    os.makedirs(paths["dir"], exist_ok=True)

    # Grava upload
    try:
        written = _safe_copy_upload_to_path(up, paths["orig"])
        if written <= 0:
            raise BadRequest("Arquivo vazio.")
    except BadRequest:
        raise
    except Exception:
        current_app.logger.exception("Falha ao salvar upload no destino")
        return jsonify({"error": "Falha ao salvar o arquivo."}), 500

    # Valida conteúdo como PDF (tolerante a BOM/bytes anteriores)
    try:
        _ensure_pdf(paths["orig"])
    except BadRequest as e:
        current_app.logger.info("Upload rejeitado: nao parecia PDF (%s)", e.description)
        raise

    # Valida MIME real como sinal auxiliar (mas não bloqueia se header ok)
    try:
        sniff = _sniff_mime_file(paths["orig"])
        if sniff not in ("application/pdf", "application/x-pdf", "application/acrobat", "application/octet-stream"):
            current_app.logger.info("MIME suspeito no upload: %s (aceito por header PDF)", sniff)
    except Exception:
        pass

    # Define current.pdf
    shutil.copyfile(paths["orig"], paths["cur"])

    # Salva meta (sem dados sensíveis)
    try:
        meta = {
            "original_name": orig_name,
            "stored_name": filename,
            "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            "user_agent": request.headers.get("User-Agent", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "size": os.path.getsize(paths["orig"]),
        }
        with open(paths["meta"], "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        try:
            os.chmod(paths["meta"], 0o600)
        except Exception:
            pass
    except Exception:
        current_app.logger.warning("Não foi possível salvar meta.json para a sessão %s", session_id)

    # Confere nº de páginas e limites
    with fitz.open(paths["cur"]) as doc:
        pages = doc.page_count
        max_pages = int(os.getenv("EDIT_MAX_PAGES", "500"))
        if pages > max_pages:
            try:
                shutil.rmtree(paths["dir"], ignore_errors=True)
            finally:
                pass
            raise BadRequest(f"PDF com muitas páginas ({pages}). Limite: {max_pages}.")

    return jsonify({"session_id": session_id, "pages": pages}), 200

# ===== Upload de imagem para overlay =====
@edit_bp.post("/api/edit/overlay-image/upload")
def api_edit_overlay_image_upload():
    """
    Upload de imagem (PNG/JPEG) para ser inserida como overlay.
    Form-Data:
      - session_id
      - image (arquivo)
    Resposta: { ok, image_id, width, height }
    """
    sid = _safe_session_id((request.form.get("session_id") or "").strip())
    img = request.files.get("image")
    if not img or not img.filename:
        raise BadRequest("Imagem ausente.")

    paths = _paths(sid)
    os.makedirs(paths["ovl"], exist_ok=True)

    provisional = os.path.join(paths["ovl"], f"up_{uuid.uuid4().hex}")
    size = _safe_copy_upload_to_path(img, provisional)
    if size <= 0:
        try: os.remove(provisional)
        except Exception: pass
        raise BadRequest("Imagem vazia.")

    max_bytes = int(os.getenv("EDIT_OVERLAY_IMAGE_MAX", str(10 * 1024 * 1024)))  # 10MB padrão
    if size > max_bytes:
        try: os.remove(provisional)
        except Exception: pass
        raise BadRequest("Arquivo de imagem muito grande.")

    mime = _sniff_mime_file(provisional)
    # Fallback: aceite o MIME do navegador se plausível
    browser_mime = (getattr(img, "mimetype", "") or "").lower()
    if mime == "application/octet-stream" and browser_mime in {"image/png","image/jpeg"}:
        mime = browser_mime

    current_app.logger.info("overlay-image sniff: %s (%d bytes)", mime, size)

    if mime not in {"image/png", "image/jpeg"}:
        try: os.remove(provisional)
        except Exception: pass
        raise BadRequest("Formato de imagem não suportado. Use PNG ou JPEG.")

    ext = ".png" if mime == "image/png" else ".jpg"
    image_id = f"img_{uuid.uuid4().hex[:12]}{ext}"
    final_path = os.path.join(paths["ovl"], secure_filename(image_id))
    try:
        os.replace(provisional, final_path)
    except Exception:
        shutil.copyfile(provisional, final_path)
        try: os.remove(provisional)
        except Exception: pass

    # Mede dimensões usando PyMuPDF (rápido e sem Pillow)
    width = height = 0
    try:
        pm = fitz.Pixmap(final_path)
        width, height = pm.width, pm.height
        del pm
    except Exception:
        pass

    return jsonify({"ok": True, "image_id": image_id, "width": width, "height": height}), 200

@edit_bp.post("/api/edit/apply/<action>")
def api_edit_apply(action):
    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        raise BadRequest("session_id ausente.")
    session_id = _safe_session_id(session_id)

    action = (action or "").lower()
    if action not in {"organize","crop","redact","text","ocr","all"}:
        raise BadRequest("Ação inválida.")

    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Sessão não encontrada.")

    # ---------- ORGANIZE (reordenar / rotacionar) ----------
    if action == "organize":
        order = data.get("order") or []          # lista 1-based das páginas ORIGINAIS na nova ordem
        rotations = data.get("rotations") or {}  # dict { "orig_page_num(1-based)": grau_absoluto }

        tmp_out = _tmp_pdf_path(paths["dir"])
        with fitz.open(paths["cur"]) as doc:
            # Reordena / remove usando select (0-based)
            if order:
                sel = []
                for n in order:
                    try:
                        idx0 = int(n) - 1
                    except Exception:
                        continue
                    if 0 <= idx0 < doc.page_count:
                        sel.append(idx0)
                if sel:
                    doc.select(sel)

            # Aplica rotação ABSOLUTA por página
            def _apply_rotation(page_obj, deg):
                d = int(deg) % 360
                if d < 0:
                    d += 360
                if d in (0, 90, 180, 270):
                    try:
                        page_obj.set_rotation(d)     # PyMuPDF >= 1.23
                    except Exception:
                        try:
                            page_obj.setRotation(d)  # versões antigas
                        except Exception:
                            pass

            if rotations:
                if order:
                    # mapear: índice NOVO -> número original 1-based
                    for new_idx, orig_1based in enumerate(order):
                        rv = rotations.get(str(orig_1based))
                        if rv is None:
                            try:
                                rv = rotations.get(int(orig_1based))
                            except Exception:
                                rv = None
                        if rv is not None and 0 <= new_idx < doc.page_count:
                            _apply_rotation(doc[new_idx], rv)
                else:
                    # sem reordenação: aplicar nos índices originais
                    for k, v in rotations.items():
                        try:
                            idx0 = int(k) - 1
                        except Exception:
                            continue
                        if 0 <= idx0 < doc.page_count:
                            _apply_rotation(doc[idx0], v)

            doc.save(tmp_out, deflate=True, garbage=3, clean=True, incremental=False)

        _sanitize_pdf(tmp_out, paths["cur"])
        return jsonify({
            "ok": True,
            "download_url": url_for("edit_bp.api_edit_download", session_id=session_id),
            "preview_refresh": url_for("edit_bp.api_edit_file", session_id=session_id)
        }), 200

    # ---------- OCR (implementado) ----------
    if action == "ocr":
        # parser booleano robusto
        def _b(v, default):
            if v is None:
                return default
            return str(v).strip().lower() in {"1", "true", "yes", "on"}

        lang         = (data.get("lang") or "").strip() or None
        force        = _b(data.get("force"), False)
        skip_text    = _b(data.get("skip_text"), True)  # padrão: não mexer em páginas com texto
        deskew       = _b(data.get("deskew"), True)
        rotate_pages = _b(data.get("rotate_pages"), True)
        clean        = _b(data.get("clean"), True)

        try:
            optimize = int(data.get("optimize") or 2)
        except Exception:
            optimize = 2

        # parâmetros opcionais de desempenho/limite
        def _int_or_none(x):
            try:
                return int(x) if x is not None else None
            except Exception:
                return None

        jobs    = _int_or_none(data.get("jobs"))
        timeout = _int_or_none(data.get("timeout"))
        mem_mb  = _int_or_none(data.get("mem_mb"))

        tmp_out = _tmp_pdf_path(paths["dir"])

        # 1) Tenta usar o service dedicado (preferido)
        if _HAS_OCR_SERVICE:
            try:
                _ocr_pdf_path(
                    paths["cur"], tmp_out,
                    lang=lang, force=force, skip_text=skip_text, optimize=optimize,
                    deskew=deskew, rotate_pages=rotate_pages, clean=clean,
                    jobs=jobs, timeout=timeout, mem_mb=mem_mb,
                )
            except BadRequest:
                raise
            except Exception as e:
                current_app.logger.exception("Falha ao executar OCR (service)")
                raise BadRequest(f"OCR falhou: {e}")
        else:
            # 2) Fallback: chama ocrmypdf diretamente em sandbox (resolve comando no Windows)
            try:
                from app.services.sandbox import run_in_sandbox
            except Exception as e:
                raise BadRequest(f"Sandbox indisponível para OCR: {e}")

            import sys, shlex, shutil

            def _resolve_cmd() -> list[str]:
                env_bin = (os.environ.get("OCR_BIN") or "").strip().strip('"').strip("'")
                if env_bin:
                    return shlex.split(env_bin)
                if os.name == "nt":
                    return [sys.executable, "-m", "ocrmypdf"]
                exe = shutil.which("ocrmypdf")
                return [exe or "ocrmypdf"]

            ocr_cmd = _resolve_cmd()
            langs = (lang or os.environ.get("OCR_LANGS") or "por+eng").strip()
            to = timeout if isinstance(timeout, int) else int(os.environ.get("OCR_TIMEOUT", "300") or 300)
            mem = mem_mb if isinstance(mem_mb, int) else int(os.environ.get("OCR_MEM_MB", "1024") or 1024)
            j = str(jobs if jobs else os.environ.get("OCR_JOBS", "1") or "1").strip()
            try:
                opt = max(0, min(3, int(optimize)))
            except Exception:
                opt = 2

            args = ocr_cmd + ["--output-type", "pdf", "--optimize", str(opt), "--jobs", j, "--language", langs]
            args.append("--rotate-pages" if rotate_pages else "--no-rotate-pages")
            if deskew:
                args.append("--deskew")
            if clean:
                args.extend(["--clean", "--thresholding", "otsu"])
            if force:
                args.append("--force-ocr")
            elif skip_text:
                args.append("--skip-text")
            args.extend([paths["cur"], tmp_out])

            try:
                proc = run_in_sandbox(args, timeout=to, cpu_seconds=to, mem_mb=mem)
            except Exception as e:
                raise BadRequest(f"Falha ao executar OCR: {e}")

            rc = getattr(proc, "returncode", 1)
            if rc != 0 or not os.path.exists(tmp_out):
                err = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "")[:800]
                raise BadRequest(f"OCR falhou (rc={rc}). {err}")

        # Sanitiza e aplica ao current.pdf
        _sanitize_pdf(tmp_out, paths["cur"])
        return jsonify({
            "ok": True,
            "download_url": url_for("edit_bp.api_edit_download", session_id=session_id),
            "preview_refresh": url_for("edit_bp.api_edit_file", session_id=session_id)
        }), 200

    # ---------- fallback (mantém comportamento antigo) ----------
    tmp_out = _tmp_pdf_path(paths["dir"])
    with fitz.open(paths["cur"]) as doc:
        doc.save(tmp_out, deflate=True, garbage=3, incremental=False, clean=True)

    _sanitize_pdf(tmp_out, paths["cur"])
    return jsonify({
        "download_url": url_for("edit_bp.api_edit_download", session_id=session_id),
        "filename": "editado.pdf",
        "preview_refresh": url_for("edit_bp.api_edit_file", session_id=session_id)
    })

# ===== overlay (whiteout + texto + imagem em lote) =====
MAX_OPS = 200
MAX_TEXT_LEN = 5000

@edit_bp.post("/api/edit/overlay")
def api_edit_overlay():
    data = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        raise BadRequest("session_id ausente.")
    session_id = _safe_session_id(session_id)

    ops = data.get("ops") or []
    if not isinstance(ops, list) or not ops:
        raise BadRequest("ops ausentes.")
    if len(ops) > MAX_OPS:
        raise BadRequest("Muitas operações.")

    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Sessão não encontrada.")

    pw = float(data.get("page_width") or 0)
    ph = float(data.get("page_height") or 0)
    if pw <= 1 or ph <= 1:
        raise BadRequest("Dimensões inválidas.")

    tmp_out = _tmp_pdf_path(paths["dir"])
    with fitz.open(paths["cur"]) as doc:
        any_redact = False
        for op in ops:
            pidx = int(op.get('pageIndex', 0))
            if not (0 <= pidx < doc.page_count):
                continue
            page = doc[pidx]
            scale_x = page.rect.width  / pw
            scale_y = page.rect.height / ph

            t = (op.get('type') or '').lower()
            if t == 'whiteout':
                x,y,w,h = [float(v) for v in op.get('rect', [0,0,0,0])]
                r = fitz.Rect(x*scale_x, y*scale_y, (x+w)*scale_x, (y+h)*scale_y)
                page.draw_rect(r, color=(1,1,1), fill=(1,1,1))
            elif t == 'redact':
                x,y,w,h = [float(v) for v in op.get('rect', [0,0,0,0])]
                r = fitz.Rect(x*scale_x, y*scale_y, (x+w)*scale_x, (y+h)*scale_y)
                try:
                    page.add_redact_annot(r, fill=(1,1,1)); any_redact = True
                except Exception:
                    page.draw_rect(r, color=(1,1,1), fill=(1,1,1))
            elif t == 'text':
                text = (op.get('text') or '')[:MAX_TEXT_LEN]
                if not text.strip():
                    continue
                size = float(_clamp_num(op.get('size', 14), 6, 96))
                color = _parse_hex_color(op.get('color', '#000000'))
                x = float(op.get('x', 0)) * scale_x
                y = float(op.get('y', 0)) * scale_y
                for i, line in enumerate(text.splitlines() or ['']):
                    page.insert_text((x, y + i*size*1.25), line, fontsize=size, color=color, fontname="helv")
            elif t == 'image':
                image_id = (op.get('image_id') or '').strip()
                ix, iy, iw, ih = [float(v) for v in op.get('rect', [0,0,0,0])]
                rect = fitz.Rect(ix*scale_x, iy*scale_y, (ix+iw)*scale_x, (iy+ih)*scale_y)
                img_path = os.path.join(paths["ovl"], secure_filename(image_id))
                if not img_path.startswith(paths["ovl"]) or not os.path.exists(img_path):
                    continue
                with open(img_path, "rb") as fh:
                    data_bytes = fh.read()
                rotate = int(op.get('rotate', 0) or 0) % 360
                page.insert_image(rect, stream=data_bytes, keep_proportion=False, rotate=rotate)

        if any_redact:
            try:
                doc.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception:
                pass

        doc.save(tmp_out, deflate=True, garbage=3, clean=True, incremental=False)

    _sanitize_pdf(tmp_out, paths["cur"])
    return jsonify({
        "ok": True,
        "session_id": session_id,
        "download_url": url_for("edit_bp.api_edit_download", session_id=session_id),
        "preview_refresh": url_for("edit_bp.api_edit_file", session_id=session_id)
    }), 200

# ===== aplicar overlays vindos do front moderno =====
@edit_bp.post("/api/edit/apply/overlays")
def api_edit_apply_overlays():
    """
    Espera:
    {
      "session_id": "...",
      "page_index": 0,  # 0-based
      "operations": { ... }
    }
    """
    import json as _json

    j = request.get_json(silent=True) or {}
    v = request.values

    session_id = (v.get("session_id") or j.get("session_id") or "").strip()
    if not session_id:
        raise BadRequest("session_id ausente.")
    session_id = _safe_session_id(session_id)

    page_index_raw = (
        v.get("page_index") or v.get("page_idx") or v.get("page_number")
        or j.get("page_index") or j.get("page_idx") or j.get("page_number")
    )
    if page_index_raw is None:
        raise BadRequest("page_index ausente.")
    try:
        page_index = int(page_index_raw)
        if (("page_number" in v and v.get("page_number") is not None) or
            ("page_number" in j and j.get("page_number") is not None)):
            page_index -= 1
    except Exception:
        raise BadRequest("page_index inválido.")

    ops_raw = v.get("operations") or v.get("ops") or j.get("operations") or j.get("ops") or {}
    if isinstance(ops_raw, str):
        try:
            ops = _json.loads(ops_raw) if ops_raw else {}
        except Exception:
            raise BadRequest("operations não é JSON válido.")
    else:
        ops = ops_raw

    def _ops_list(d, *names):
        for k in names:
            val = d.get(k)
            if isinstance(val, list):
                return val
        return []

    whiteouts = _ops_list(ops, "whiteouts", "whiteout")
    redacts   = _ops_list(ops, "redacts", "redact")
    texts     = _ops_list(ops, "texts", "text")
    images    = _ops_list(ops, "images", "image")
    options   = ops.get("options") or {}

    fill_rgb = _parse_hex_color(options.get("color", "#FFFFFF"))
    fill_alpha = _clamp_num(options.get("alpha", 1.0), 0.0, 1.0)

    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Sessão não encontrada.")

    tmp_out = _tmp_pdf_path(paths["dir"])
    with fitz.open(paths["cur"]) as doc:
        if not (0 <= page_index < doc.page_count):
            raise BadRequest("page_index fora do intervalo.")
        page = doc[page_index]
        W, H = page.rect.width, page.rect.height

        any_redact = False

        # -------- BORRACHA (whiteout achatado) --------
        for r in whiteouts:
            try:
                x0 = _clamp_num(r.get("x0", 0), 0.0, 1.0)
                y0 = _clamp_num(r.get("y0", 0), 0.0, 1.0)
                x1 = _clamp_num(r.get("x1", 0), 0.0, 1.0)
                y1 = _clamp_num(r.get("y1", 0), 0.0, 1.0)
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                rect = fitz.Rect(x0 * W, y0 * H, x1 * W, y1 * H)
            except Exception:
                continue
            try:
                page.draw_rect(rect, color=fill_rgb, fill=fill_rgb, fill_opacity=fill_alpha)
            except TypeError:
                page.draw_rect(rect, color=fill_rgb, fill=fill_rgb)

        # -------- REDAÇÃO --------
        for r in redacts:
            try:
                x0 = _clamp_num(r.get("x0", 0), 0.0, 1.0)
                y0 = _clamp_num(r.get("y0", 0), 0.0, 1.0)
                x1 = _clamp_num(r.get("x1", 0), 0.0, 1.0)
                y1 = _clamp_num(r.get("y1", 0), 0.0, 1.0)
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                rect = fitz.Rect(x0 * W, y0 * H, x1 * W, y1 * H)
            except Exception:
                continue
            try:
                page.add_redact_annot(rect, fill=(1, 1, 1)); any_redact = True
            except Exception:
                page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))

        # -------- TEXTOS --------
        for t in texts:
            txt = (t.get("text") or "").strip()
            if not txt:
                continue
            x  = _clamp_num(t.get("x", 0), 0.0, 1.0) * W
            y  = _clamp_num(t.get("y", 0), 0.0, 1.0) * H
            if t.get("size_rel") is not None:
                size_px = float(t.get("size_rel")) * H
            else:
                size_px = float(t.get("size", 14) or 14)
            size_px = max(6.0, min(96.0, size_px))
            for i, line in enumerate(txt.splitlines() or [""]):
                page.insert_text(
                    fitz.Point(x, y + i * size_px * 1.2),
                    line,
                    fontsize=size_px,
                    color=(0, 0, 0),
                    fontname="helv",
                )

        # -------- IMAGENS --------
        for im in images:
            image_id = secure_filename((im.get("image_id") or "").strip())
            if not image_id:
                continue
            try:
                x0 = _clamp_num(im.get("x0", 0), 0.0, 1.0)
                y0 = _clamp_num(im.get("y0", 0), 0.0, 1.0)
                x1 = _clamp_num(im.get("x1", 0), 0.0, 1.0)
                y1 = _clamp_num(im.get("y1", 0), 0.0, 1.0)
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
                rect = fitz.Rect(x0 * W, y0 * H, x1 * W, y1 * H)
            except Exception:
                continue

            img_path = os.path.join(paths["ovl"], image_id)
            if os.path.commonpath([paths["ovl"], os.path.abspath(img_path)]) != os.path.abspath(paths["ovl"]):
                continue
            if not os.path.exists(img_path):
                continue

            try:
                with open(img_path, "rb") as fh:
                    data_bytes = fh.read()
                rotate = int(im.get("rotate", 0) or 0) % 360
                page.insert_image(rect, stream=data_bytes, keep_proportion=False, rotate=rotate)
            except Exception:
                current_app.logger.exception("Falha ao inserir imagem %s", image_id)

        if any_redact:
            try:
                doc.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception:
                pass

        doc.save(tmp_out, deflate=True, garbage=3, clean=True, incremental=False)

    _sanitize_pdf(tmp_out, paths["cur"])
    return jsonify({
        "ok": True,
        "session_id": session_id,
        "page_index": page_index,
        "download_url": url_for("edit_bp.api_edit_download", session_id=session_id),
        "preview_refresh": url_for("edit_bp.api_edit_file", session_id=session_id),
    }), 200

@edit_bp.get("/api/edit/download/<session_id>")
def api_edit_download(session_id):
    session_id = _safe_session_id(session_id)
    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Resultado não encontrado.")
    return send_file(
        paths["cur"],
        mimetype="application/pdf",
        as_attachment=True,
        download_name="editado.pdf",
        max_age=0
    )

@edit_bp.get("/api/edit/file/<session_id>")
def api_edit_file(session_id):
    session_id = _safe_session_id(session_id)
    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Arquivo da sessão não encontrado.")
    return send_file(paths["cur"], mimetype="application/pdf", as_attachment=False, max_age=0)

# ===== fechar sessão =====
@edit_bp.post("/api/edit/close")
def api_edit_close():
    data = request.get_json(silent=True) or {}
    sid = _safe_session_id(data.get("session_id", ""))

    sdir = _session_dir(sid)
    try:
        root = os.path.join(current_app.config['UPLOAD_FOLDER'], 'edit_sessions')
        os.makedirs(root, exist_ok=True)
        if os.path.commonpath([root, os.path.abspath(sdir)]) != os.path.abspath(root):
            raise BadRequest("Caminho inválido.")
        shutil.rmtree(sdir, ignore_errors=True)
        current_app.logger.info("Sessão %s encerrada e limpa.", sid)
    except BadRequest:
        raise
    except Exception:
        current_app.logger.exception("Falha ao limpar sessão %s", sid)

    return jsonify({"ok": True, "session_id": sid})

# ===== imagem nítida =====
@edit_bp.get("/api/edit/page-image/<session_id>/<int:page_number>")
def api_edit_page_image(session_id, page_number: int):
    session_id = _safe_session_id(session_id)

    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Sessão não encontrada.")

    try:
        scale = float(request.args.get("scale", "2.0") or 2.0)
    except Exception:
        scale = 2.0
    scale = max(0.5, min(4.0, scale))

    with fitz.open(paths["cur"]) as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise NotFound("Página inválida.")
        page = doc[page_number - 1]

        W, H = page.rect.width, page.rect.height
        max_side = 4096.0
        est_w = W * scale
        est_h = H * scale
        if est_w > max_side or est_h > max_side:
            factor = min(max_side / est_w, max_side / est_h)
            scale *= factor

        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        buf = io.BytesIO(pix.tobytes("png"))
        buf.seek(0)
        return send_file(buf, mimetype="image/png", max_age=0)