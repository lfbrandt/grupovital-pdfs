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

edit_bp = Blueprint("edit_bp", __name__)

ALLOWED_MODES = ("organize", "crop", "redact", "text", "ocr", "all")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")  # defensivo

# -------- helpers --------
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
    }

def _is_pdf_bytes(b: bytes) -> bool:
    return isinstance(b, (bytes, bytearray)) and b.startswith(b"%PDF-")

def _ensure_pdf(path: str):
    with open(path, "rb") as f:
        head = f.read(5)
    if not _is_pdf_bytes(head):
        raise BadRequest("Arquivo enviado não é um PDF válido.")

def _safe_copy_upload_to_path(up_file_storage, dest_path: str, chunk_size: int = 1024 * 1024) -> None:
    try:
        up_file_storage.stream.seek(0)
    except Exception:
        pass
    with open(dest_path, "wb") as out:
        src = up_file_storage.stream
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
    try:
        os.chmod(dest_path, 0o600)
    except Exception:
        pass

def _safe_session_id(sid: str) -> str:
    sid = (sid or "").strip()
    if not SESSION_ID_RE.match(sid):
        raise BadRequest("session_id inválido.")
    return sid

# ---------- pikepdf compat ----------
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
    up = request.files.get("file")
    if not up or not up.filename:
        raise BadRequest("Nenhum arquivo enviado.")

    filename = secure_filename(up.filename or "upload.pdf")
    if not filename.lower().endswith(".pdf"):
        raise BadRequest("Envie um arquivo PDF.")

    session_id = uuid.uuid4().hex[:12]
    paths = _paths(session_id)
    os.makedirs(paths["dir"], exist_ok=True)

    try:
        _safe_copy_upload_to_path(up, paths["orig"])
    except Exception:
        current_app.logger.exception("Falha ao salvar upload no destino")
        return jsonify({"error": "Falha ao salvar o arquivo."}), 500

    _ensure_pdf(paths["orig"])
    shutil.copyfile(paths["orig"], paths["cur"])

    try:
        meta = {
            "original_name": filename,
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

@edit_bp.post("/api/edit/apply/<action>")
def api_edit_apply(action):
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        raise BadRequest("session_id ausente.")
    session_id = _safe_session_id(session_id)

    action = (action or "").lower()
    if action not in {"organize","crop","redact","text","ocr","all"}:
        raise BadRequest("Ação inválida.")

    paths = _paths(session_id)
    if not os.path.exists(paths["cur"]):
        raise NotFound("Sessão não encontrada.")

    with fitz.open(paths["cur"]) as doc:
        if action == "organize":
            pages_in  = data.get("pages") or []      # 1-based
            rotations = data.get("rotations") or {}  # {"1":90,"3":270} ABSOLUTAS
            order_in  = data.get("order") or []      # 1-based
            delete    = bool(data.get("delete", False))
            rotate    = int(data.get("rotate", 0) or 0)

            # rotações ABSOLUTAS
            if isinstance(rotations, dict):
                for k, v in rotations.items():
                    try:
                        idx1 = int(k); rot = int(v) % 360
                        pi = idx1 - 1
                        if 0 <= pi < doc.page_count:
                            doc[pi].set_rotation(rot)
                    except Exception:
                        continue

            # rotação em lote (delta)
            if rotate and pages_in:
                for pi in [(int(p) - 1) for p in pages_in if int(p) >= 1]:
                    if 0 <= pi < doc.page_count:
                        cur = doc[pi].rotation
                        doc[pi].set_rotation((cur + rotate) % 360)

            # reordenação/remoção
            if order_in:
                new_order = []
                for p in order_in:
                    try:
                        pi = int(p) - 1
                    except Exception:
                        continue
                    if 0 <= pi < doc.page_count:
                        new_order.append(pi)
                if new_order:
                    doc.select(new_order)
            else:
                if delete and pages_in:
                    for pi in sorted({int(p) - 1 for p in pages_in if int(p) >= 1}, reverse=True):
                        if 0 <= pi < doc.page_count:
                            doc.delete_page(pi)
                elif pages_in:
                    new_order = []
                    for p in pages_in:
                        try:
                            pi = int(p) - 1
                        except Exception:
                            continue
                        if 0 <= pi < doc.page_count:
                            new_order.append(pi)
                    if new_order:
                        doc.select(new_order)

        elif action == "crop":
            rects = data.get("rects") or []  # [{page,x0,y0,x1,y1}]  page 0-based (normalizado 0..1)
            for r in rects:
                try:
                    pi = int(r.get("page", 0))
                    if not (0 <= pi < doc.page_count):
                        continue
                    page = doc[pi]
                    W, H = page.rect.width, page.rect.height
                    x0, y0, x1, y1 = [float(r[k]) for k in ("x0","y0","x1","y1")]
                    x0, x1 = min(x0, x1), max(x0, x1)
                    y0, y1 = min(y0, y1), max(y0, y1)
                    rect = fitz.Rect(x0*W, y0*H, x1*W, y1*H)
                    page.set_cropbox(rect)
                    try:
                        page.set_mediabox(rect)
                    except Exception:
                        pass
                except Exception:
                    continue

        elif action == "redact":
            rects = data.get("rects") or []  # normalizado 0..1
            for r in rects:
                try:
                    pi = int(r.get("page", 0))
                    if not (0 <= pi < doc.page_count):
                        continue
                    page = doc[pi]
                    W, H = page.rect.width, page.rect.height
                    x0, y0, x1, y1 = [float(r[k]) for k in ("x0","y0","x1","y1")]
                    rect = fitz.Rect(x0*W, y0*H, x1*W, y1*H)
                    page.add_redact_annot(rect, fill=(1,1,1))
                except Exception:
                    continue
            try:
                doc.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception:
                pass

        elif action == "text":
            text = (data.get("text") or "").strip()
            size = int(data.get("size", 14) or 14)
            pos  = data.get("pos") or {"page":0,"x":0.1,"y":0.1}
            pi = int(pos.get("page", 0) or 0)
            if pi >= 1:
                pi -= 1
            if 0 <= pi < doc.page_count and text:
                page = doc[pi]
                W, H = page.rect.width, page.rect.height
                x = float(pos.get("x", 0.1)) * W
                y = float(pos.get("y", 0.1)) * H
                page.insert_text(fitz.Point(x, y), text, fontsize=size, color=(0,0,0), fontname="helv")

        elif action == "ocr":
            return jsonify({"message": "OCR será habilitado em fase posterior."}), 202

        tmp_out = os.path.join(paths["dir"], f"tmp_{uuid.uuid4().hex[:8]}.pdf")
        doc.save(tmp_out, deflate=True, garbage=3, incremental=False)

    _sanitize_pdf(tmp_out, paths["cur"])
    return jsonify({
        "download_url": url_for("edit_bp.api_edit_download", session_id=session_id),
        "filename": "editado.pdf",
        "preview_refresh": url_for("edit_bp.api_edit_file", session_id=session_id)
    })

# ===== overlay (whiteout + texto em lote) =====
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

    tmp_out = tempfile.mkstemp(suffix='.pdf')[1]
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
                try:
                    x,y,w,h = [float(v) for v in op.get('rect', [0,0,0,0])]
                except Exception:
                    continue
                r = fitz.Rect(x*scale_x, y*scale_y, (x+w)*scale_x, (y+h)*scale_y)
                try:
                    page.add_redact_annot(r, fill=(1,1,1))
                    any_redact = True
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

# ===== NOVO: fechar sessão e limpar diretório =====
@edit_bp.post("/api/edit/close")
def api_edit_close():
    """
    Encerra a sessão do editor e remove os arquivos temporários.
    Body JSON: { "session_id": "<sid>" }
    """
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

# ===== imagem nítida da página para o editor =====
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