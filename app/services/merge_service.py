# app/services/merge_service.py
# -*- coding: utf-8 -*-
import os
import tempfile
import platform
import hashlib
import shutil
import uuid
from typing import List, Optional, Dict, Any, Tuple, Set

import pikepdf
from flask import current_app
from werkzeug.exceptions import BadRequest
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.errors import PdfReadError
from PyPDF2.generic import NameObject, NumberObject, RectangleObject

from ..utils.config_utils import ensure_upload_folder_exists
from ..utils.limits import (
    enforce_pdf_page_limit,
    enforce_total_pages,
)
from ..utils.pdf_utils import cleanup_upload_files
from .sandbox import run_in_sandbox
from .sanitize_service import sanitize_pdf

# Aviso exibido quando assinatura digital é detectada
_SIGNATURE_WARNING = (
    "Detectamos assinatura digital em um ou mais PDFs. "
    "Ao juntar documentos, a validade criptográfica da assinatura pode ser invalidada. "
    "A ferramenta tentou preservar a aparência visual da assinatura no arquivo final."
)

# =========================
# Ghostscript configuration
# =========================
env_gs = os.environ.get("GS_BIN") or os.environ.get("GHOSTSCRIPT_BIN")
if env_gs:
    GHOSTSCRIPT_BIN = env_gs
elif platform.system() == "Windows":
    GHOSTSCRIPT_BIN = "gswin64c"
else:
    GHOSTSCRIPT_BIN = "gs"

_GS_TO = os.environ.get("GS_TIMEOUT") or os.environ.get("GHOSTSCRIPT_TIMEOUT") or "60"
GHOSTSCRIPT_TIMEOUT = int(_GS_TO)

SIZES_PT = {
    "A4":     (595.2756, 841.8898),
    "LETTER": (612.0, 792.0),
}


# ──────────────────────────────────────────────────────────────────────────────
# Detecção de assinatura digital
# ──────────────────────────────────────────────────────────────────────────────

def _has_sig_field(fields: Any, depth: int = 0) -> bool:
    if depth > 20:
        return False
    try:
        for field_ref in fields:
            try:
                field = field_ref.get_object() if hasattr(field_ref, "get_object") else field_ref
                if field.get("/FT") == pikepdf.Name("/Sig"):
                    return True
                kids = field.get("/Kids", pikepdf.Array())
                if kids and _has_sig_field(kids, depth + 1):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def detect_pdf_signatures(input_path: str) -> bool:
    """
    Detecta indícios de assinatura digital sem validação criptográfica.
    Verifica /SigFlags, /FT==Sig em /Fields, e /Annots Widget/Sig.
    Nunca lança exceção — erros retornam False (conservador).
    """
    try:
        with pikepdf.open(input_path, suppress_warnings=True) as pdf:
            root = pdf.Root
            if "/AcroForm" not in root:
                return False
            acroform = root["/AcroForm"]

            if "/SigFlags" in acroform:
                try:
                    if int(acroform["/SigFlags"]) & 1:
                        current_app.logger.debug(
                            "[merge_service] Assinatura via /SigFlags: %s",
                            os.path.basename(input_path),
                        )
                        return True
                except Exception:
                    pass

            fields = acroform.get("/Fields", pikepdf.Array())
            if fields and _has_sig_field(fields):
                current_app.logger.debug(
                    "[merge_service] Assinatura via /Fields: %s",
                    os.path.basename(input_path),
                )
                return True

            for page in pdf.pages:
                for annot_ref in page.get("/Annots", pikepdf.Array()):
                    try:
                        annot = annot_ref.get_object() if hasattr(annot_ref, "get_object") else annot_ref
                        if (annot.get("/Subtype") == pikepdf.Name("/Widget")
                                and annot.get("/FT") == pikepdf.Name("/Sig")):
                            current_app.logger.debug(
                                "[merge_service] Assinatura via /Annots Widget: %s",
                                os.path.basename(input_path),
                            )
                            return True
                    except Exception:
                        continue
    except Exception as exc:
        current_app.logger.debug(
            "[merge_service] detect_pdf_signatures erro (ignorado): %s — %s",
            os.path.basename(input_path), exc,
        )
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de ângulo e boxes
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_angle(angle: int) -> int:
    try:
        a = int(angle)
    except Exception:
        raise BadRequest(f"Ângulo inválido: {angle}")
    a %= 360
    if a < 0:
        a += 360
    if a not in (0, 90, 180, 270):
        raise BadRequest(f"Ângulo inválido: {angle} (use 0/90/180/270)")
    return a


def _mb_tuple(page) -> Tuple[float, float, float, float]:
    mb = page.mediabox
    return float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)


def _rect_like_to_tuple(page, rect_obj) -> Tuple[float, float, float, float]:
    obj = rect_obj
    try:
        if hasattr(obj, "get_object"):
            obj = obj.get_object()
    except Exception:
        pass
    try:
        if isinstance(obj, RectangleObject):
            llx = float(obj.left)
            lly = float(obj.bottom)
            urx = float(obj.right)
            ury = float(obj.top)
        else:
            seq = list(obj)
            llx, lly, urx, ury = float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3])
    except Exception:
        llx, lly, urx, ury = _mb_tuple(page)
    x1, x2 = sorted((llx, urx))
    y1, y2 = sorted((lly, ury))
    return x1, y1, x2, y2


def _get_box(page, name: str):
    r = page.get(NameObject(name))
    return r if r is not None else page.mediabox


def _union_boxes(page) -> Tuple[float, float, float, float]:
    rects = [
        page.mediabox,
        _get_box(page, "/CropBox"),
        _get_box(page, "/TrimBox"),
        _get_box(page, "/BleedBox"),
        _get_box(page, "/ArtBox"),
    ]
    tups = [_rect_like_to_tuple(page, r) for r in rects]
    return (
        min(t[0] for t in tups),
        min(t[1] for t in tups),
        max(t[2] for t in tups),
        max(t[3] for t in tups),
    )


def _set_box(page, box_name: str, llx: float, lly: float, urx: float, ury: float):
    page[NameObject(box_name)] = RectangleObject((llx, lly, urx, ury))


def _reset_and_rotate(page, angle: Optional[int], keep_crop: bool = False):
    llx, lly, urx, ury = _union_boxes(page)
    if angle is not None:
        page[NameObject("/Rotate")] = NumberObject(0)
        if angle:
            try:
                page.rotate(angle)
            except Exception:
                page.rotate_clockwise(angle)
    _set_box(page, "/MediaBox", llx, lly, urx, ury)
    if not keep_crop:
        for b in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            _set_box(page, b, llx, lly, urx, ury)
    else:
        for b in ("/TrimBox", "/BleedBox", "/ArtBox"):
            _set_box(page, b, llx, lly, urx, ury)


# ──────────────────────────────────────────────────────────────────────────────
# Ghostscript helpers
# ──────────────────────────────────────────────────────────────────────────────

def _flatten_pdf(input_path: str, pdf_settings: str) -> str:
    dirname, filename = os.path.split(input_path)
    flat_name = filename.replace(".pdf", f"_flat_{pdf_settings.replace('/', '')}.pdf")
    flat_path = os.path.join(dirname, flat_name)
    cmd = [
        GHOSTSCRIPT_BIN,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        f"-dPDFSETTINGS={pdf_settings}",
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        "-dNOPAUSE", "-dBATCH", "-dQUIET",
        "-dSAFER",
        f"-sOutputFile={flat_path}",
        input_path,
    ]
    current_app.logger.debug("[merge_service] GS flatten: %s", " ".join(cmd))
    try:
        run_in_sandbox(cmd, timeout=GHOSTSCRIPT_TIMEOUT, cpu_seconds=60, mem_mb=768)
    except Exception as e:
        raise BadRequest(f"Falha ao flattenar PDF: {e}")
    return flat_path


def _normalize_pages_gs(input_path: str, page_size: str = "A4") -> str:
    page_size = (page_size or "A4").upper()
    width_pt, height_pt = SIZES_PT.get(page_size, SIZES_PT["A4"])
    dirname, filename = os.path.split(input_path)
    out_name = filename.replace(".pdf", f"_norm_{page_size}.pdf")
    out_path = os.path.join(dirname, out_name)
    cmd = [
        GHOSTSCRIPT_BIN,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        "-dPDFFitPage",
        "-dFIXEDMEDIA",
        f"-dDEVICEWIDTHPOINTS={int(round(width_pt))}",
        f"-dDEVICEHEIGHTPOINTS={int(round(height_pt))}",
        "-dAutoRotatePages=/None",
        "-dNOPAUSE", "-dBATCH", "-dQUIET",
        "-dSAFER",
        f"-sOutputFile={out_path}",
        input_path,
    ]
    current_app.logger.debug("[merge_service] GS normalize: %s", " ".join(cmd))
    try:
        run_in_sandbox(cmd, timeout=max(GHOSTSCRIPT_TIMEOUT, 60), cpu_seconds=60, mem_mb=768)
    except Exception as e:
        raise BadRequest(f"Falha ao normalizar páginas: {e}")
    return out_path


# ──────────────────────────────────────────────────────────────────────────────
# Validação e pipeline de saída
# ──────────────────────────────────────────────────────────────────────────────

def _validate_pdf_integrity(path: str, label: str = "") -> None:
    tag = f" ({label})" if label else ""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"[merge_service] PDF inválido{tag}: arquivo ausente ou vazio.")
    try:
        with pikepdf.open(path, suppress_warnings=True) as p:
            if len(p.pages) == 0:
                raise RuntimeError(f"[merge_service] PDF sem páginas{tag}.")
    except pikepdf.PdfError as exc:
        raise RuntimeError(f"[merge_service] PDF inválido estruturalmente{tag}: {exc}") from exc


_MERGE_DEBUG = os.environ.get("MERGE_DEBUG", "").strip() == "1"


def _probe_stage(label: str, src: str, upload_folder: str) -> None:
    if not _MERGE_DEBUG:
        return
    try:
        debug_dir = os.path.join(upload_folder, "merge_debug")
        os.makedirs(debug_dir, exist_ok=True)
        dst = os.path.join(debug_dir, f"{label}.pdf")
        shutil.copy2(src, dst)
        size = os.path.getsize(dst)
        try:
            with pikepdf.open(dst, suppress_warnings=True) as _p:
                status = f"OK pages={len(_p.pages)}"
        except Exception as _e:
            status = f"PIKEPDF_ERROR: {_e}"
        current_app.logger.warning("[merge_debug] %s size=%d %s", label, size, status)
    except Exception as _e:
        current_app.logger.warning("[merge_debug] probe %s FAILED: %s", label, _e)


def _rebuild_with_pikepdf(src_path: str, dst_path: str) -> None:
    with pikepdf.open(src_path, suppress_warnings=True) as pdf:
        pdf.save(
            dst_path,
            linearize=False,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            compress_streams=True,
        )


def _sanitize_output(path: str) -> str:
    """
    Pipeline de saída: validação → rebuild pikepdf → sanitização → validação.
    Preserva /AcroForm e /Annots (remove_annotations=False, preserve_acroform=True)
    para manter aparência visual de assinaturas. Não garante validade criptográfica.
    """
    tag = os.path.basename(path)
    upload_folder = os.path.dirname(path)

    _validate_pdf_integrity(path, label=f"pre-rebuild/{tag}")
    current_app.logger.debug("[merge_service] pré-rebuild OK: %s", tag)
    _probe_stage("stage1_writer", path, upload_folder)

    rebuilt_path = path + ".rebuilt.pdf"
    try:
        _rebuild_with_pikepdf(path, rebuilt_path)
        current_app.logger.debug("[merge_service] rebuild OK: %s", tag)
        _probe_stage("stage2_rebuilt", rebuilt_path, upload_folder)
    except Exception as exc:
        current_app.logger.error(
            "[merge_service] rebuild FALHOU (%s): %s", tag, exc,
        )
        try:
            os.remove(rebuilt_path)
        except OSError:
            pass
        raise RuntimeError(f"Não foi possível reconstruir o PDF: {exc}") from exc

    sanitized_path = path + ".san.pdf"
    try:
        sanitize_pdf(
            rebuilt_path,
            sanitized_path,
            remove_annotations=False,
            remove_actions=True,
            remove_embedded=True,
            preserve_acroform=True,
        )
        current_app.logger.debug("[merge_service] sanitize OK: %s", tag)
        _probe_stage("stage3_sanitized", sanitized_path, upload_folder)
    except Exception as exc:
        current_app.logger.error(
            "[merge_service] sanitize FALHOU (%s): %s — usando rebuilt", tag, exc,
        )
        try:
            shutil.copy2(rebuilt_path, sanitized_path)
        except OSError:
            pass
        _probe_stage("stage3_sanitized_FALLBACK", sanitized_path, upload_folder)
    finally:
        try:
            os.remove(rebuilt_path)
        except OSError:
            pass

    try:
        os.replace(sanitized_path, path)
    except OSError:
        shutil.copy2(sanitized_path, path)
        try:
            os.remove(sanitized_path)
        except OSError:
            pass

    _validate_pdf_integrity(path, label=f"final/{tag}")
    current_app.logger.debug("[merge_service] PDF final validado: %s", tag)
    _probe_stage("stage4_final", path, upload_folder)

    return path


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de seleção de páginas
# ──────────────────────────────────────────────────────────────────────────────

def _collect_page_sizes(pdf_path: str) -> Set[Tuple[int, int]]:
    sizes: Set[Tuple[int, int]] = set()
    r = PdfReader(pdf_path)
    for p in r.pages:
        w = int(round(float(p.mediabox.width)))
        h = int(round(float(p.mediabox.height)))
        sizes.add((w, h))
    return sizes


def _has_rotated_pages(pdf_path: str) -> bool:
    r = PdfReader(pdf_path)
    for p in r.pages:
        try:
            rot = int(p.get("/Rotate", 0) or 0) % 360
            if rot in (90, 270):
                return True
        except Exception:
            return True
    return False


def _normalize_page_index(p: int, total: int) -> int:
    if 1 <= p <= total:
        return p - 1
    if 0 <= p < total:
        return p
    raise BadRequest(f"Índice de página inválido: {p} (total={total})")


def _normalize_pages_selection(pages: Optional[List[int]], total: int) -> List[int]:
    if not pages:
        return list(range(total))
    if all(1 <= v <= total for v in pages):
        return [v - 1 for v in pages]
    if all(0 <= v < total for v in pages):
        return pages
    raise BadRequest(f"Índices de página inválidos: {pages}")


def _extract_and_write_page(
    reader: PdfReader,
    page_idx: int,
    writer: PdfWriter,
    angle_abs: Optional[int] = None,
    crop: Optional[List[float]] = None,
    auto_orient: bool = False,
) -> None:
    page = reader.pages[page_idx]
    desired_abs: Optional[int] = None
    if angle_abs is not None:
        val = _normalize_angle(angle_abs)
        if (val % 360) != 0:
            desired_abs = val
    elif auto_orient:
        try:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            rot = int(page.get("/Rotate", 0) or 0) % 360
            if w > h and rot in (0, 180):
                desired_abs = (rot + 90) % 360
        except Exception:
            desired_abs = None
    _reset_and_rotate(page, desired_abs, keep_crop=bool(crop))
    if crop:
        if not (isinstance(crop, list) and len(crop) == 4):
            raise BadRequest("Crop inválido; esperado [x1,y1,x2,y2].")
        x1, y1, x2, y2 = map(float, crop)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        page.cropbox = RectangleObject((x1, y1, x2, y2))
    writer.add_page(page)


# ──────────────────────────────────────────────────────────────────────────────
# Função principal de merge
# ──────────────────────────────────────────────────────────────────────────────

def merge_selected_pdfs(
    file_paths: List[str],
    plan: Optional[List[Dict[str, Any]]] = None,
    pages_map: Optional[List[List[int]]] = None,
    rotations_map: Optional[List[List[int]]] = None,
    flatten: bool = False,
    pdf_settings: str = "/ebook",
    auto_orient: bool = False,
    crops: Optional[List[List[dict]]] = None,
    *,
    normalize: str = "auto",
    norm_page_size: str = "A4",
) -> Tuple[str, List[str]]:
    """
    Junta PDFs com rotação absoluta e boxes normalizados.

    Retorna:
        Tuple[str, List[str]]: (caminho_do_pdf_merged, lista_de_avisos)

    Avisos incluem detecção de assinatura digital e desativação de flatten.
    Segurança: sanitização pikepdf por arquivo, limites, sandbox GS (-dSAFER).
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    processed_inputs: List[str] = []
    total_selected_pages = 0
    warnings: List[str] = []
    signed_indices: Set[int] = set()
    readers: List[PdfReader] = []

    try:
        # 1) Sanitiza cada entrada antes de qualquer leitura/merge.
        # Fail-closed: se uma entrada falha, nenhuma posterior e processada.
        for idx, path in enumerate(file_paths):
            sanitized = os.path.join(
                upload_folder,
                f"san_{hashlib.md5((str(path) + str(idx)).encode()).hexdigest()}.pdf",
            )
            try:
                # Todos os arquivos usam sanitização preservadora de conteúdo visual.
                # Parâmetros explícitos para evitar remoção acidental de:
                #   - campos AcroForm preenchidos (/V, /AP)
                #   - anotações visuais (carimbos, marcações, widgets)
                #   - aparência de assinaturas digitais
                #   - imagens e conteúdo de páginas escaneadas
                # Ainda remove vetores de ataque:
                #   - JavaScript e OpenAction do catálogo
                #   - ações automáticas (/AA)
                #   - arquivos embutidos (/EmbeddedFiles)
                #   - XFA (substituído por AcroForm estático)
                sanitize_pdf(
                    path, sanitized,
                    remove_annotations=False,
                    remove_actions=True,
                    remove_embedded=True,
                    preserve_acroform=True,
                )
            except Exception as exc:
                current_app.logger.error(
                    "[merge] falha na sanitizacao: %s", type(exc).__name__
                )
                cleanup_upload_files((sanitized,), upload_folder)
                raise RuntimeError("merge_sanitize_failed") from exc

            processed_inputs.append(sanitized)

        # 1B) Detecta assinaturas e abre somente os PDFs sanitizados.
        for idx, use_path in enumerate(processed_inputs):
            has_sig = detect_pdf_signatures(use_path)
            if has_sig:
                signed_indices.add(idx)
                current_app.logger.info(
                    "[merge_service] Assinatura detectada no arquivo %d de %d.",
                    idx + 1, len(file_paths),
                )

            enforce_pdf_page_limit(use_path, label=f"arquivo_{idx + 1}")
            try:
                readers.append(PdfReader(use_path))
            except PdfReadError:
                raise BadRequest(f"Arquivo {idx + 1} é inválido ou está corrompido.")

        if signed_indices:
            warnings.append(_SIGNATURE_WARNING)

        writer = PdfWriter()

        effective_flatten = flatten
        if flatten and signed_indices:
            effective_flatten = False
            warnings.append(
                "O achatamento (flatten) foi desabilitado automaticamente porque "
                "um ou mais PDFs possuem assinatura digital. "
                "O Ghostscript remove anotações de assinatura durante o flatten."
            )
            current_app.logger.info(
                "[merge_service] flatten desabilitado: %d arquivo(s) assinado(s).",
                len(signed_indices),
            )

        # 2) Plano FLAT (ABSOLUTO)
        if plan:
            for i, item in enumerate(plan):
                src = item.get("src")
                page = item.get("page")
                angle_abs: Optional[int] = None
                if "rotation" in item and item.get("rotation") is not None:
                    try:
                        _raw = int(item.get("rotation"))
                        if (_raw % 360) != 0:
                            angle_abs = _normalize_angle(_raw)
                    except Exception:
                        angle_abs = None
                crop = item.get("crop") if "crop" in item else None
                if not isinstance(src, int) or not (0 <= src < len(readers)):
                    raise BadRequest(f"'src' inválido no plan item {i}.")
                if not isinstance(page, int):
                    raise BadRequest(f"'page' inválido no plan item {i}.")
                reader = readers[src]
                total = len(reader.pages)
                pidx = _normalize_page_index(page, total)
                total_selected_pages += 1
                enforce_total_pages(total_selected_pages)
                _extract_and_write_page(reader, pidx, writer,
                                        angle_abs=angle_abs, crop=crop, auto_orient=False)

        # 3) Legado por arquivo
        else:
            rotations_map = rotations_map or []
            crops = crops or [[] for _ in range(len(readers))]
            for src_idx, reader in enumerate(readers):
                total = len(reader.pages)
                raw_pages = pages_map[src_idx] if (pages_map and src_idx < len(pages_map)) else None
                page_indices = _normalize_pages_selection(raw_pages, total)
                rots = rotations_map[src_idx] if src_idx < len(rotations_map) else []
                file_crops = crops[src_idx] if src_idx < len(crops) else []
                for j, pidx in enumerate(page_indices):
                    user_angle: Optional[int] = None
                    raw_val = rots[j] if j < len(rots) else None
                    if raw_val is not None:
                        try:
                            _v = int(raw_val)
                            if (_v % 360) != 0:
                                user_angle = _normalize_angle(_v)
                        except Exception:
                            user_angle = None
                    crop_box = None
                    for rec in file_crops:
                        rec_page = rec.get("page", 0)
                        rec_idx = (rec_page - 1) if isinstance(rec_page, int) and rec_page >= 1 else rec_page
                        if rec_idx == pidx:
                            crop_box = rec.get("box")
                            break
                    total_selected_pages += 1
                    enforce_total_pages(total_selected_pages)
                    _extract_and_write_page(reader, pidx, writer,
                                            angle_abs=user_angle, crop=crop_box,
                                            auto_orient=auto_orient)

        # 4) Escreve merge em disco
        merged_path = os.path.join(upload_folder, f"merge_{uuid.uuid4().hex}.pdf")
        with open(merged_path, "wb") as _fh:
            writer.write(_fh)
        if not os.path.exists(merged_path) or os.path.getsize(merged_path) == 0:
            raise RuntimeError("PyPDF2 gerou arquivo de merge vazio.")

        # 5) Normalização de tamanho de página
        mode = (normalize or "auto").lower()
        need_norm = False
        if mode == "on":
            need_norm = True
        elif mode == "auto":
            sizes = _collect_page_sizes(merged_path)
            rotated = _has_rotated_pages(merged_path)
            need_norm = (len(sizes) > 1) and (not rotated)
            if not need_norm:
                reason = "rotated-pages" if rotated else "uniform-size"
                current_app.logger.debug("[merge_service] normalize=auto SKIP (%s)", reason)

        if need_norm:
            normed = _normalize_pages_gs(merged_path, page_size=norm_page_size)
            try:
                os.remove(merged_path)
            except OSError:
                pass
            merged_path = normed

        # 6) Sem flatten
        if not effective_flatten:
            current_app.logger.debug(
                "[merge_service] merged (normalize=%s, flatten=False): %s", mode, merged_path,
            )
            return _sanitize_output(merged_path), warnings

        # 7) Com flatten + cache
        with open(merged_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        cache_dir = current_app.config.get("MERGE_CACHE_DIR", tempfile.gettempdir())
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{digest}_{pdf_settings.replace('/', '')}.pdf")

        if os.path.exists(cache_file):
            current_app.logger.debug("[merge_service] Cache hit: %s", cache_file)
            try:
                os.remove(merged_path)
            except OSError:
                pass
            tmp_copy = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
            tmp_copy.close()
            shutil.copy(cache_file, tmp_copy.name)
            return _sanitize_output(tmp_copy.name), warnings

        flat_merged = _flatten_pdf(merged_path, pdf_settings)
        try:
            shutil.copy(flat_merged, cache_file)
        except OSError:
            current_app.logger.warning("[merge_service] Falha ao escrever cache: %s", cache_file)
        finally:
            try:
                os.remove(merged_path)
            except OSError:
                current_app.logger.warning(
                    "[merge_service] Não removeu intermediário: %s", merged_path
                )

        return _sanitize_output(flat_merged), warnings

    finally:
        # Fechar todos os PdfReader explicitamente antes de remover arquivos.
        # No Windows, streams abertos bloqueiam os.remove(); ignorar falhas individuais.
        for r in readers:
            try:
                if hasattr(r, 'stream') and r.stream and not r.stream.closed:
                    r.stream.close()
            except Exception:
                pass
        for p in processed_inputs:
            try:
                os.remove(p)
            except OSError:
                pass
