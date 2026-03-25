import io
import os
import shutil
import time
import uuid
import base64
import json
import subprocess
import tempfile
from flask import Blueprint, request, jsonify, send_file, after_this_request, current_app

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter

from PIL import Image
from io import BytesIO

from ..services.compress_service import (
    comprimir_pdf,
    comprimir_pdf_com_params,
    USER_PROFILES,
    _get_ghostscript_cmd,
    _apply_rotations_pikepdf,
    enrich_page_analysis,
)
from ..services.sanitize_service import sanitize_pdf
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from .. import limiter

# ── Sessões de análise — armazenamento em disco ───────────────────────────────
# _ANALYSE_SESSIONS (dict em memória) quebra com Gunicorn multi-worker porque
# cada worker tem seu próprio espaço de memória. A request de analyze pode cair
# no Worker A e a de process-with-settings no Worker B → KeyError → HTTP 404.
#
# Solução: gravar o mapeamento analyse_id → filepath em um arquivo .session
# no próprio UPLOAD_FOLDER. Todos os workers leem/escrevem o mesmo disco.
# TTL é enforçado na leitura (_session_get) e na limpeza periódica (_purge).
_SESSION_TTL_SECONDS: int = 3600  # 1 hora


def _session_path(analyse_id: str, upload_folder: str) -> str:
    """Caminho do arquivo de sessão para um dado analyse_id."""
    return os.path.join(upload_folder, f".session_{analyse_id}")


def _session_set(analyse_id: str, pdf_path: str, upload_folder: str) -> None:
    """Persiste analyse_id → (pdf_path, timestamp) em disco."""
    data = json.dumps({"path": pdf_path, "ts": time.time()})
    sess_file = _session_path(analyse_id, upload_folder)
    try:
        with open(sess_file, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as e:
        current_app.logger.error("[session] falha ao gravar sessão %s: %s", analyse_id, e)
        raise


def _session_get(analyse_id: str) -> str | None:
    """
    Lê sessão do disco. Retorna o pdf_path se válido, None se expirado/ausente.
    Compatível com todos os workers Gunicorn (leitura de disco compartilhado).
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    sess_file = _session_path(analyse_id, upload_folder)
    if not os.path.exists(sess_file):
        return None
    try:
        with open(sess_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        ts   = float(data.get("ts", 0))
        path = data.get("path", "")
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if time.time() - ts > _SESSION_TTL_SECONDS:
        _session_delete(analyse_id, upload_folder)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return None
    if not os.path.exists(path):
        _session_delete(analyse_id, upload_folder)
        return None
    return path


def _session_delete(analyse_id: str, upload_folder: str) -> None:
    """Remove o arquivo de sessão do disco (não o PDF — responsabilidade do caller)."""
    sess_file = _session_path(analyse_id, upload_folder)
    try:
        os.remove(sess_file)
    except OSError:
        pass


def _purge_expired_sessions() -> None:
    """
    Varre o UPLOAD_FOLDER em busca de arquivos .session expirados e os remove,
    junto com os PDFs associados. Chamado no início de cada /analyze.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    cutoff = time.time() - _SESSION_TTL_SECONDS
    try:
        for fname in os.listdir(upload_folder):
            if not fname.startswith(".session_"):
                continue
            fpath = os.path.join(upload_folder, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ts   = float(data.get("ts", 0))
                path = data.get("path", "")
                if ts < cutoff:
                    os.remove(fpath)
                    if path and os.path.exists(path):
                        os.remove(path)
            except (OSError, json.JSONDecodeError, ValueError):
                try:
                    os.remove(fpath)
                except OSError:
                    pass
    except OSError as e:
        current_app.logger.warning("[session] _purge falhou: %s", e)


compress_bp = Blueprint("compress", __name__, url_prefix="/api/compress")


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize_profile(p: str) -> str:
    p = (p or "").strip().lower()
    return p if p in USER_PROFILES else "equilibrio"


def _normalize_pages(pages_raw):
    if not pages_raw:
        return None
    if isinstance(pages_raw, str):
        try:
            pages_raw = json.loads(pages_raw)
        except json.JSONDecodeError:
            raise ValueError("pages/order deve ser JSON válido (lista de inteiros 1-based)")
    if pages_raw is None:
        return None
    if not isinstance(pages_raw, list):
        raise ValueError("pages/order deve ser uma lista de inteiros (1-based)")
    out = []
    for p in pages_raw:
        try:
            n = int(p)
            if n >= 1:
                out.append(n)
        except Exception:
            raise ValueError("pages/order deve conter apenas inteiros")
    return out or None


def _normalize_rotations(rot_raw):
    if rot_raw is None or rot_raw == "":
        return None
    if isinstance(rot_raw, str):
        try:
            rot_raw = json.loads(rot_raw)
        except json.JSONDecodeError:
            raise ValueError("rotations/rot deve ser JSON válido (lista ou objeto)")
    out = {}
    if isinstance(rot_raw, dict):
        for k, v in rot_raw.items():
            try:
                page_1b = int(k)
                deg = int(v) % 360
                if deg < 0:
                    deg += 360
                if deg not in (0, 90, 180, 270):
                    deg = (round(deg / 90) * 90) % 360
                if deg != 0:
                    out[page_1b] = deg
            except Exception:
                continue
    elif isinstance(rot_raw, list):
        for idx0, v in enumerate(rot_raw):
            try:
                deg = int(v) % 360
                if deg < 0:
                    deg += 360
                if deg not in (0, 90, 180, 270):
                    deg = (round(deg / 90) * 90) % 360
                page_1b = idx0 + 1
                if deg != 0:
                    out[page_1b] = deg
            except Exception:
                continue
    else:
        raise ValueError("rotations/rot deve ser lista ou objeto JSON")
    return out or None


def _json_error(message: str, status: int = 400):
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


def _extract_pdf_metadata(file_path: str) -> dict:
    try:
        with open(file_path, "rb") as f:
            reader = PdfReader(f)
            total_pages = len(reader.pages)
            total_size_bytes = os.path.getsize(file_path)
            page_areas = []
            total_area = 0
            for idx in range(total_pages):
                mb = reader.pages[idx].mediabox
                w, h = float(mb.width), float(mb.height)
                area = w * h
                page_areas.append((w, h, area))
                total_area += area
            avg_area = total_area / total_pages if total_pages > 0 else 1
            pages = []
            for idx, (w, h, area) in enumerate(page_areas):
                estimated_kb = (area / total_area) * (total_size_bytes / 1024) if total_area > 0 else 0
                pages.append({
                    "page_number":       idx + 1,
                    "width":             round(w, 1),
                    "height":            round(h, 1),
                    "area":              area,
                    "estimated_size_kb": round(estimated_kb, 1),
                    "is_large":          area > (avg_area * 1.3),
                })
            return {
                "total_pages":      total_pages,
                "total_size_bytes": total_size_bytes,
                "total_size_mb":    str(round(total_size_bytes / (1024 * 1024), 1)),
                "pages":            pages,
            }
    except Exception as e:
        current_app.logger.exception("Erro ao extrair metadados PDF: %s", e)
        raise


def _generate_page_thumbnail(pdf_path: str, page_index: int) -> str:
    """
    Gera JPEG de thumbnail a 144 DPI (240×338 px) para nitidez em DPR até 2×.
    Única definição — sem versão antiga com parâmetro quality.
    """
    page_num = page_index + 1
    temp_png = None
    try:
        gs_cmd = _get_ghostscript_cmd() or "gs"
        temp_fd, temp_png = tempfile.mkstemp(suffix=".png", prefix="gs_thumb_")
        os.close(temp_fd)
        cmd = [
            gs_cmd, "-dNOPAUSE", "-dBATCH", "-dSAFER",
            "-sDEVICE=png16m", "-r144",
            f"-dFirstPage={page_num}", f"-dLastPage={page_num}",
            f"-sOutputFile={temp_png}", pdf_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30, text=True)
        if not os.path.exists(temp_png):
            raise RuntimeError(f"Ghostscript não gerou PNG para página {page_num}")
        with Image.open(temp_png) as img:
            img.thumbnail((240, 338), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=88)
            jpeg_bytes = buf.getvalue()
        return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
    except Exception as e:
        current_app.logger.warning("Thumbnail página %d falhou: %s — usando placeholder", page_num, e)
        svg = (
            f'<svg width="200" height="280" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="200" height="280" fill="#eee"/>'
            f'<text x="100" y="140" font-family="Arial" font-size="14" '
            f'fill="#999" text-anchor="middle" dy=".3em">Página {page_num}</text></svg>'
        )
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode("ascii")
    finally:
        if temp_png and os.path.exists(temp_png):
            try:
                os.remove(temp_png)
            except OSError:
                pass


# ── endpoints ─────────────────────────────────────────────────────────────────

@compress_bp.route("", methods=["POST"])
@compress_bp.route("/", methods=["POST"])
@limiter.limit("5 per minute")
def compress():
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
            return _json_error("modificacoes deve ser JSON válido", 400)
    try:
        pages = _normalize_pages(
            request.form.get("pages") or request.form.get("order") or request.form.get("page_order")
        )
    except ValueError as e:
        return _json_error(str(e), 400)
    raw_rot = (
        request.form.get("rotations") or request.form.get("rot")
        or request.headers.get("X-Rotations")
    )
    try:
        rotations = _normalize_rotations(raw_rot)
    except ValueError as e:
        return _json_error(str(e), 400)
    try:
        out_path = comprimir_pdf(f, pages=pages, rotations=rotations,
                                 modificacoes=modificacoes, profile=profile)

        @after_this_request
        def _cleanup(resp):
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            return resp

        return send_file(out_path, mimetype="application/pdf",
                         as_attachment=False, download_name=os.path.basename(out_path))
    except Exception:
        current_app.logger.exception("Erro comprimindo PDF")
        return _json_error("Falha ao comprimir o PDF.", 500)


@compress_bp.get("/profiles")
def list_profiles():
    return jsonify({k: {"label": v["label"], "hint": v["hint"]} for k, v in USER_PROFILES.items()})


@compress_bp.post("/analyze")
@limiter.limit("10 per minute")
def analyze():
    f = request.files.get("file")
    if not f or not f.filename:
        return _json_error("Nenhum arquivo PDF enviado.", 400)
    if not f.filename.lower().endswith(".pdf"):
        return _json_error("O arquivo deve ser um PDF (.pdf).", 400)
    f.seek(0)
    header = f.read(4)
    f.seek(0)
    if header != b"%PDF":
        return _json_error("O arquivo não é um PDF válido.", 400)

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)
    try:
        clean_filename = validate_upload(f, {"pdf"})
    except Exception as e:
        current_app.logger.warning("Validação de upload falhou: %s", e)
        return _json_error("Arquivo não passou na validação.", 400)

    temp_id       = uuid.uuid4().hex
    temp_path     = os.path.join(upload_folder, f"analyze_{temp_id}_{clean_filename}")
    analysis_path = None

    try:
        f.save(temp_path)
        sanitized_path = os.path.join(upload_folder, f"sanitized_{temp_id}_{clean_filename}")
        try:
            sanitize_pdf(temp_path, sanitized_path)
            analysis_path = sanitized_path
            try:
                os.remove(temp_path)
            except OSError:
                pass
        except Exception as e:
            current_app.logger.warning("[analyze] sanitização falhou, usando original: %s", e)
            analysis_path = temp_path

        metadata  = _extract_pdf_metadata(analysis_path)
        has_large = False
        pages_data = []

        for page_meta in metadata["pages"]:
            if page_meta["is_large"]:
                has_large = True
            thumb = _generate_page_thumbnail(analysis_path, page_meta["page_number"] - 1)
            # Defaults neutros — serão sobrescritos por enrich_page_analysis abaixo
            pages_data.append({
                "page_number":       page_meta["page_number"],
                "width":             page_meta["width"],
                "height":            page_meta["height"],
                "estimated_size_kb": page_meta["estimated_size_kb"],
                "is_large":          page_meta["is_large"],
                "area":              page_meta["area"],
                "thumbnail":         thumb,
                "quality":           80,
                "dpi":               100,
                "include":           True,
                "resize_to_a4":      False,
                "keep_original":     False,
            })

        # enrich_page_analysis é a única fonte de verdade para quality/dpi/resize sugeridos.
        # Calcula size_factor, is_large refinado, quality_suggested, dpi_suggested por página.
        pages_data = enrich_page_analysis(pages_data)

        analyse_id = uuid.uuid4().hex
        _purge_expired_sessions()
        _session_set(analyse_id, analysis_path, upload_folder)

        return jsonify({
            "analyse_id":      analyse_id,
            "filename":        clean_filename,
            "total_pages":     metadata["total_pages"],
            "total_size_mb":   metadata["total_size_mb"],
            "has_large_pages": has_large,
            "pages":           pages_data,
        }), 200

    except Exception as e:
        current_app.logger.exception("[analyze] Erro: %s", e)
        for p in [temp_path, analysis_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return _json_error("Falha ao analisar o PDF. Tente novamente.", 500)


@compress_bp.post("/process-with-settings")
@limiter.limit("5 per minute")
def process_with_settings():
    data = request.get_json(silent=True)
    if not data:
        return _json_error("Payload JSON inválido ou ausente.", 400)

    analyse_id    = data.get("analyse_id", "").strip()
    page_settings = data.get("page_settings")
    rotations_raw = data.get("rotations")

    if not analyse_id:
        return _json_error("analyse_id é obrigatório.", 400)
    if not page_settings or not isinstance(page_settings, list):
        return _json_error("page_settings deve ser uma lista.", 400)

    source_path = _session_get(analyse_id)
    if not source_path:
        return _json_error("Sessão expirada ou não encontrada. Faça upload novamente.", 404)

    settings_by_page = {}
    for s in page_settings:
        try:
            pn = int(s.get("page_number", 0))
            if pn < 1:
                continue
            settings_by_page[pn] = {
                "include":       bool(s.get("include", True)),
                "quality":       max(20, min(100, int(s.get("quality",  80)))),
                "dpi":           max(50, min(300, int(s.get("dpi",     100)))),
                "resize_to_a4":  bool(s.get("resize_to_a4",  False)),
                "keep_original": bool(s.get("keep_original", False)),
            }
        except (TypeError, ValueError):
            continue

    included_pages = sorted(pn for pn, s in settings_by_page.items() if s["include"])
    if not included_pages:
        return _json_error("Nenhuma página selecionada para incluir.", 400)

    try:
        rotations = _normalize_rotations(rotations_raw) if rotations_raw else None
    except ValueError as e:
        return _json_error(str(e), 400)

    pages_keep = [pn for pn in included_pages if settings_by_page[pn]["keep_original"]]
    compress_groups: dict = {}
    for pn in included_pages:
        s = settings_by_page[pn]
        if s["keep_original"]:
            continue
        key = (s["quality"], s["dpi"], s["resize_to_a4"])
        compress_groups.setdefault(key, []).append(pn)

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    out_path    = None
    group_files = []

    try:
        compressed_page_bytes: dict = {}

        for (quality, dpi, resize_to_a4), group_pages in compress_groups.items():
            group_rotations = None
            if rotations:
                group_rotations = {pn: deg for pn, deg in rotations.items()
                                   if pn in group_pages} or None
            group_out = os.path.join(upload_folder, f"group_{uuid.uuid4().hex}.pdf")
            group_files.append(group_out)
            comprimir_pdf_com_params(
                input_path=source_path, output_path=group_out,
                pages=group_pages, quality=quality, dpi=dpi,
                resize_to_a4=resize_to_a4, rotations=group_rotations,
            )
            with open(group_out, "rb") as fg:
                rg = PdfReader(fg)
                for idx, pn in enumerate(group_pages):
                    if idx >= len(rg.pages):
                        continue
                    wt = PdfWriter()
                    wt.add_page(rg.pages[idx])
                    buf = io.BytesIO()
                    wt.write(buf)
                    compressed_page_bytes[pn] = buf.getvalue()

        if not pages_keep and len(compress_groups) == 1:
            out_path = group_files[0]
        else:
            writer = PdfWriter()
            orig_page_bytes: dict = {}

            if pages_keep:
                keep_rots = ({pn: rotations[pn] for pn in pages_keep
                              if pn in rotations} if rotations else {})
                keep_extracted = os.path.join(upload_folder, f"keep_{uuid.uuid4().hex}.pdf")
                group_files.append(keep_extracted)
                _apply_rotations_pikepdf(
                    src_pdf=source_path, pages=pages_keep,
                    rotations=keep_rots or None, out_pdf=keep_extracted,
                )
                with open(keep_extracted, "rb") as fk:
                    rk = PdfReader(fk)
                    for idx, pn in enumerate(pages_keep):
                        if idx >= len(rk.pages):
                            continue
                        wt = PdfWriter()
                        wt.add_page(rk.pages[idx])
                        buf = io.BytesIO()
                        wt.write(buf)
                        orig_page_bytes[pn] = buf.getvalue()

            for pn in included_pages:
                if pn in orig_page_bytes:
                    writer.add_page(PdfReader(io.BytesIO(orig_page_bytes[pn])).pages[0])
                elif pn in compressed_page_bytes:
                    writer.add_page(PdfReader(io.BytesIO(compressed_page_bytes[pn])).pages[0])

            out_path = os.path.join(upload_folder, f"merged_{uuid.uuid4().hex}.pdf")
            with open(out_path, "wb") as f_out:
                writer.write(f_out)

        # ── Fallback final ─────────────────────────────────────────────────────
        original_size = os.path.getsize(source_path)
        final_size    = os.path.getsize(out_path)

        if final_size >= original_size:
            fallback_type = "final_original"
            shutil.copyfile(source_path, out_path)
            final_size = os.path.getsize(out_path)
            current_app.logger.info(
                "[process-with-settings] fallback=final_original "
                "original=%.1f KB merged=%.1f KB — entregando original",
                original_size / 1024, final_size / 1024,
            )
        else:
            fallback_type = "none"
            current_app.logger.info(
                "[process-with-settings] OK original=%.1f KB final=%.1f KB reduction=%.1f%%",
                original_size / 1024, final_size / 1024,
                (1 - final_size / original_size) * 100,
            )

        reduction_pct = 0.0 if fallback_type != "none" else round(
            (1 - final_size / original_size) * 100, 1
        )

        _session_delete(analyse_id, upload_folder)
        try:
            os.remove(source_path)
        except OSError:
            pass

        @after_this_request
        def _cleanup(resp):
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            return resp

        response = send_file(out_path, mimetype="application/pdf",
                             as_attachment=True, download_name="comprimido.pdf")
        response.headers["X-Size-Original-KB"] = str(round(original_size / 1024, 1))
        response.headers["X-Size-Final-KB"]    = str(round(final_size / 1024, 1))
        response.headers["X-Reduction-Pct"]    = str(reduction_pct)
        response.headers["X-Fallback"]         = fallback_type
        # Garante que proxies reversos (Nginx) não filtrem os headers X-* customizados.
        # Necessário para que fetch() em produção consiga ler esses valores.
        response.headers["Access-Control-Expose-Headers"] = (
            "X-Size-Original-KB, X-Size-Final-KB, X-Reduction-Pct, X-Fallback"
        )
        return response

    except Exception:
        current_app.logger.exception(
            "[process-with-settings] Erro — analyse_id=%s", analyse_id)
        return _json_error("Falha ao processar o PDF. Tente novamente.", 500)

    finally:
        for p in [gf for gf in group_files if gf != out_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass