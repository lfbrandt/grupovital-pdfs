# -*- coding: utf-8 -*-
import os
import tempfile
import platform
import hashlib
import shutil
from typing import List, Optional, Dict, Any, Tuple

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
from .sandbox import run_in_sandbox            # ✅ sandbox seguro
from .sanitize_service import sanitize_pdf     # ✅ sanitização de entrada


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


def _normalize_angle(angle: int) -> int:
    """Normaliza ângulo para {0,90,180,270} aceitando negativos/múltiplos de 90."""
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


# -------------------- helpers de boxes robustos --------------------
def _mb_tuple(page) -> Tuple[float, float, float, float]:
    mb = page.mediabox
    return float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)


def _rect_like_to_tuple(page, rect_obj) -> Tuple[float, float, float, float]:
    """
    Converte qualquer tipo aceitável de box (RectangleObject, ArrayObject,
    lista/tupla, ou objeto indireto) para (llx, lly, urx, ury) em float,
    com ordenação normalizada.
    """
    obj = rect_obj
    # Resolve indireto, se houver (algumas libs criam IndirectObject)
    try:
        if hasattr(obj, "get_object"):
            obj = obj.get_object()
    except Exception:
        pass

    try:
        if isinstance(obj, RectangleObject):
            llx, lly, urx, ury = float(obj.left), float(obj.bottom), float(obj.right), float(obj.top)
        else:
            seq = list(obj)  # ArrayObject, lista, tupla…
            llx, lly, urx, ury = float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3])
    except Exception:
        # Fallback: MediaBox
        llx, lly, urx, ury = _mb_tuple(page)

    # Normaliza ordem (alguns geradores invertem limites)
    x1, x2 = sorted((llx, urx))
    y1, y2 = sorted((lly, ury))
    return x1, y1, x2, y2


def _get_box(page, name: str):
    r = page.get(NameObject(name))
    return r if r is not None else page.mediabox


def _union_boxes(page) -> Tuple[float, float, float, float]:
    """União geométrica entre Media/Crop/Trim/Bleed/Art (coordenadas correntes)."""
    rects = [
        page.mediabox,
        _get_box(page, "/CropBox"),
        _get_box(page, "/TrimBox"),
        _get_box(page, "/BleedBox"),
        _get_box(page, "/ArtBox"),
    ]
    tups = [_rect_like_to_tuple(page, r) for r in rects]
    llx = min(t[0] for t in tups)
    lly = min(t[1] for t in tups)
    urx = max(t[2] for t in tups)
    ury = max(t[3] for t in tups)
    return llx, lly, urx, ury


def _set_box(page, box_name: str, llx: float, lly: float, urx: float, ury: float):
    page[NameObject(box_name)] = RectangleObject((llx, lly, urx, ury))


def _reset_and_rotate(page, angle: int, keep_crop: bool = False):
    """
    Rotação ABSOLUTA + normalização de boxes SEM mudar a origem real.
    - Usa a UNIÃO de todos os boxes já existentes para evitar clipping.
    - Em 90/270, apenas definimos /Rotate e mantemos as dimensões;
      o viewer aplica a rotação.
    """
    # União atual dos boxes
    llx, lly, urx, ury = _union_boxes(page)

    # Zera /Rotate e aplica rotação absoluta
    page[NameObject("/Rotate")] = NumberObject(0)
    if angle:
        try:
            page.rotate(angle)
        except Exception:
            page.rotate_clockwise(angle)

    # MediaBox = união → nada fica fora da área visível
    _set_box(page, "/MediaBox", llx, lly, urx, ury)

    if not keep_crop:
        # Sem crop do usuário → alinhar tudo na união
        for b in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            _set_box(page, b, llx, lly, urx, ury)
    else:
        # Com crop explícito → alinhar auxiliares; /CropBox será setado depois
        for b in ("/TrimBox", "/BleedBox", "/ArtBox"):
            _set_box(page, b, llx, lly, urx, ury)


def _flatten_pdf(input_path: str, pdf_settings: str) -> str:
    """Flatten com Ghostscript (apariências/camadas) sob sandbox + -dSAFER."""
    dirname, filename = os.path.split(input_path)
    flat_name = filename.replace(".pdf", f"_flat_{pdf_settings.replace('/', '')}.pdf")
    flat_path = os.path.join(dirname, flat_name)

    cmd = [
        GHOSTSCRIPT_BIN,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        f"-dPDFSETTINGS={pdf_settings}",
        "-dAutoRotatePages=/None",        # 🔒 NÃO desvirar
        "-dDetectDuplicateImages=true",
        "-dNOPAUSE", "-dBATCH", "-dQUIET",
        "-dSAFER",
        f"-sOutputFile={flat_path}",
        input_path,
    ]
    current_app.logger.debug(f"[merge_service] Ghostscript cmd: {' '.join(cmd)}")
    try:
        run_in_sandbox(cmd, timeout=GHOSTSCRIPT_TIMEOUT, cpu_seconds=60, mem_mb=768)
    except Exception as e:
        raise BadRequest(f"Falha ao flattenar PDF: {e}")
    return flat_path


def _normalize_page_index(p: int, total: int) -> int:
    if 1 <= p <= total: return p - 1
    if 0 <= p < total:  return p
    raise BadRequest(f"Índice de página inválido: {p} (total={total})")


def _normalize_pages_selection(pages: Optional[List[int]], total: int) -> List[int]:
    if not pages: return list(range(total))
    if all(1 <= v <= total for v in pages): return [v - 1 for v in pages]
    if all(0 <= v < total for v in pages):  return pages
    raise BadRequest(f"Índices de página inválidos: {pages}")


def _extract_and_write_page(
    reader: PdfReader,
    page_idx: int,
    writer: PdfWriter,
    angle_abs: int,
    crop: Optional[List[float]] = None,
    auto_orient: bool = False,
) -> None:
    """
    Extrai page_idx do reader, aplica rotação ABSOLUTA e (opcionalmente) crop.
    - angle_abs é FINAL (0/90/180/270).
    - auto_orient só vale se angle_abs == 0.
    - Boxes normalizados por UNIÃO → sem cortes.
    """
    page = reader.pages[page_idx]

    base_angle = 0
    if auto_orient and (angle_abs % 360 == 0):
        try:
            w = float(page.mediabox.width); h = float(page.mediabox.height)
            if w > h: base_angle = 90
        except Exception:
            base_angle = 0

    final_angle = _normalize_angle(base_angle + _normalize_angle(angle_abs))

    # 1) rotação + normalização de boxes (mantendo origem real)
    _reset_and_rotate(page, final_angle, keep_crop=bool(crop))

    # 2) crop do usuário (coordenadas no sistema atual)
    if crop:
        if not (isinstance(crop, list) and len(crop) == 4):
            raise BadRequest("Crop inválido; esperado [x1,y1,x2,y2].")
        x1, y1, x2, y2 = map(float, crop)
        # normaliza caso venham invertidos
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        page.cropbox = RectangleObject((x1, y1, x2, y2))

    writer.add_page(page)


def merge_selected_pdfs(
    file_paths: List[str],
    plan: Optional[List[Dict[str, Any]]] = None,
    pages_map: Optional[List[List[int]]] = None,
    rotations_map: Optional[List[List[int]]] = None,
    flatten: bool = False,
    pdf_settings: str = "/ebook",
    auto_orient: bool = False,
    crops: Optional[List[List[dict]]] = None,
) -> str:
    """
    Junta PDFs com rotação **absoluta** e boxes normalizados.
    Segurança: pikepdf (sanitização), limites, sandbox GS (-dSAFER), sem auto-rotate.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    writer = PdfWriter()
    processed_inputs: List[str] = []
    total_selected_pages = 0

    readers: List[PdfReader] = []
    try:
        # 1) Sanitiza e abre
        for idx, path in enumerate(file_paths):
            sanitized = os.path.join(upload_folder, f"san_{hashlib.md5((str(path)+str(idx)).encode()).hexdigest()}.pdf")
            try:
                sanitize_pdf(path, sanitized)
                use_path = sanitized
            except Exception:
                use_path = path
            if use_path != path:
                processed_inputs.append(use_path)

            enforce_pdf_page_limit(use_path, label=os.path.basename(path))
            try:
                readers.append(PdfReader(use_path))
            except PdfReadError:
                raise BadRequest(f"Arquivo inválido ou corrompido: {os.path.basename(path)}")

        # 2) flat plan (ABSOLUTO)
        if plan:
            for i, item in enumerate(plan):
                src = item.get("src")
                page = item.get("page")
                angle_raw = item.get("rotation", 0)
                angle_abs = _normalize_angle(int(angle_raw or 0))
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
                                        angle_abs=angle_abs, crop=crop,
                                        auto_orient=False)  # ignora auto_orient no plan

        # 3) legado por arquivo
        else:
            rotations_map = rotations_map or []
            crops = crops or [[] for _ in range(len(readers))]

            for src_idx, reader in enumerate(readers):
                total = len(reader.pages)
                raw_pages = (pages_map[src_idx] if (pages_map and src_idx < len(pages_map)) else None)
                page_indices = _normalize_pages_selection(raw_pages, total)

                rots = rotations_map[src_idx] if src_idx < len(rotations_map) else []
                file_crops = crops[src_idx] if src_idx < len(crops) else []

                for j, pidx in enumerate(page_indices):
                    user_angle = _normalize_angle(int(rots[j] if j < len(rots) else 0))

                    crop_box = None
                    for rec in file_crops:
                        rec_page = rec.get("page", 0)
                        rec_idx = rec_page - 1 if isinstance(rec_page, int) and rec_page >= 1 else rec_page
                        if rec_idx == pidx:
                            crop_box = rec.get("box")
                            break

                    total_selected_pages += 1
                    enforce_total_pages(total_selected_pages)

                    _extract_and_write_page(reader, pidx, writer,
                                            angle_abs=user_angle, crop=crop_box,
                                            auto_orient=auto_orient)

        # 4) escreve PDF mesclado
        out_tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
        with out_tf:
            writer.write(out_tf)
        merged_path = out_tf.name

        if not flatten:
            current_app.logger.debug(f"[merge_service] PDF mesclado sem flatten: {merged_path}")
            return merged_path

        # 5) Flatten com cache
        with open(merged_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        cache_dir = current_app.config.get("MERGE_CACHE_DIR", tempfile.gettempdir())
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{digest}_{pdf_settings.replace('/', '')}.pdf")

        if os.path.exists(cache_file):
            current_app.logger.debug(f"[merge_service] Cache hit: {cache_file}")
            try: os.remove(merged_path)
            except OSError: pass
            tmp_copy = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
            tmp_copy.close()
            shutil.copy(cache_file, tmp_copy.name)
            return tmp_copy.name

        flat_merged = _flatten_pdf(merged_path, pdf_settings)
        try: shutil.copy(flat_merged, cache_file)
        except OSError: current_app.logger.warning(f"Falha ao escrever cache: {cache_file}")
        finally:
            try: os.remove(merged_path)
            except OSError: current_app.logger.warning(f"Não removeu intermediário: {merged_path}")

        return flat_merged

    finally:
        for p in processed_inputs:
            try: os.remove(p)
            except OSError: pass