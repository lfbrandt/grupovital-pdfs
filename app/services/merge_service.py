# app/services/merge_service.py
# -*- coding: utf-8 -*-
import os
import tempfile
import platform
import hashlib
import shutil
from typing import List, Optional, Dict, Any, Tuple, Set

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

# tamanhos em pontos
SIZES_PT = {
    "A4":     (595.2756, 841.8898),  # 210x297mm
    "LETTER": (612.0, 792.0),        # 8.5x11in
}

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
    try:
        if hasattr(obj, "get_object"):
            obj = obj.get_object()
    except Exception:
        pass

    try:
        if isinstance(obj, RectangleObject):
            llx, lly, urx, ury = float(obj.left), float(obj.bottom), float(obj.right), float(obj.top)
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


def _reset_and_rotate(page, angle: Optional[int], keep_crop: bool = False):
    """
    Normaliza boxes (UNIÃO) e aplica rotação **absoluta** somente
    quando `angle` não é None.

    - angle=None  → preserva /Rotate atual (não zera).
    - angle in {0,90,180,270} → zera /Rotate e aplica o valor desejado.
    - Em 90/270, definimos /Rotate e mantemos dimensões; o viewer aplica.
    """
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
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        "-dNOPAUSE", "-dBATCH", "-dQUIET",
        "-dSAFER",
        f"-sOutputFile={flat_path}",
        input_path,
    ]
    current_app.logger.debug(f"[merge_service] Ghostscript flatten: {' '.join(cmd)}")
    try:
        run_in_sandbox(cmd, timeout=GHOSTSCRIPT_TIMEOUT, cpu_seconds=60, mem_mb=768)
    except Exception as e:
        raise BadRequest(f"Falha ao flattenar PDF: {e}")
    return flat_path


def _normalize_pages_gs(input_path: str, page_size: str = "A4") -> str:
    """
    Normaliza TODAS as páginas para um tamanho fixo (A4/Letter),
    ajustando escala proporcional (modo 'contain') com -dPDFFitPage.
    """
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
        "-dAutoRotatePages=/None",   # não gira automaticamente
        "-dNOPAUSE", "-dBATCH", "-dQUIET",
        "-dSAFER",
        f"-sOutputFile={out_path}",
        input_path,
    ]
    current_app.logger.debug(f"[merge_service] Ghostscript normalize: {' '.join(cmd)}")
    try:
        run_in_sandbox(cmd, timeout=max(GHOSTSCRIPT_TIMEOUT, 60), cpu_seconds=60, mem_mb=768)
    except Exception as e:
        raise BadRequest(f"Falha ao normalizar páginas: {e}")
    return out_path


def _collect_page_sizes(pdf_path: str) -> Set[Tuple[int, int]]:
    """Retorna o conjunto de tamanhos (W,H) inteiros das páginas do PDF."""
    sizes: Set[Tuple[int, int]] = set()
    r = PdfReader(pdf_path)
    for p in r.pages:
        w = int(round(float(p.mediabox.width)))
        h = int(round(float(p.mediabox.height)))
        sizes.add((w, h))
    return sizes


def _has_rotated_pages(pdf_path: str) -> bool:
    """True se existir alguma página com /Rotate 90 ou 270."""
    r = PdfReader(pdf_path)
    for p in r.pages:
        try:
            rot = int(p.get("/Rotate", 0) or 0) % 360
            if rot in (90, 270):
                return True
        except Exception:
            # Em dúvida, seja conservador: considere como rotacionada
            return True
    return False


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
    angle_abs: Optional[int] = None,
    crop: Optional[List[float]] = None,
    auto_orient: bool = False,
) -> None:
    """
    Extrai page_idx do reader e escreve no writer aplicando, opcionalmente:
      - rotação **ABSOLUTA** (se angle_abs != None);
      - auto_orient leve (somente se angle_abs == None);
      - normalização de boxes pela UNIÃO;
      - crop (se informado).

    OBS:
    - angle_abs=None → preserva /Rotate original (não zera).
    - Se crop existir, mantemos /CropBox; Trim/Bleed/Art são normalizados.
    """
    page = reader.pages[page_idx]

    desired_abs: Optional[int] = None
    if angle_abs is not None:
        # usuário definiu rotação; 0/360 ⇒ tratamos como "sem alteração"
        val = _normalize_angle(angle_abs)
        if (val % 360) != 0:
            desired_abs = val
    elif auto_orient:
        try:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            rot = int(page.get("/Rotate", 0) or 0) % 360
            # Se a página está “deitada” (w > h) e sem rotação efetiva (0/180),
            # damos +90 em relação ao atual para ficar em pé.
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
    normalize: str = "auto",          # 'auto' | 'on' | 'off'
    norm_page_size: str = "A4",       # 'A4' | 'LETTER'
) -> str:
    """
    Junta PDFs com rotação **absoluta** e boxes normalizados.

    Comportamento:
    - Rotação só é alterada se você fornecer um valor != 0/360.
    - 'auto' para normalização de tamanho agora é *conservador*:
      só normaliza se os tamanhos diferirem **e** não houver
      nenhuma página com /Rotate 90/270 (bugs conhecidos do GS).
    - 'on' força normalização (mesmo com páginas rotacionadas).
    Segurança: sanitização pikepdf, limites, sandbox GS (-dSAFER), sem auto-rotate.
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

        # 2) plano FLAT (ABSOLUTO)
        if plan:
            for i, item in enumerate(plan):
                src = item.get("src")
                page = item.get("page")

                # 0/ausente ⇒ não alterar
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
                    # 0/None → não alterar
                    user_angle: Optional[int] = None
                    raw_val = None
                    if j < len(rots):
                        raw_val = rots[j]
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
                        rec_idx = rec_page - 1 if isinstance(rec_page, int) and rec_page >= 1 else rec_page
                        if rec_idx == pidx:
                            crop_box = rec.get("box")
                            break

                    total_selected_pages += 1
                    enforce_total_pages(total_selected_pages)

                    _extract_and_write_page(reader, pidx, writer,
                                            angle_abs=user_angle, crop=crop_box,
                                            auto_orient=auto_orient)

        # 4) escreve merge
        out_tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
        with out_tf:
            writer.write(out_tf)
        merged_path = out_tf.name

        # 5) Normalização de página (A4/Letter)
        #    'on'  -> sempre normaliza
        #    'off' -> nunca
        #    'auto'-> normaliza apenas se detectar tamanhos diferentes
        #            E NÃO houver páginas com /Rotate 90/270 (conservador)
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
                current_app.logger.debug(f"[merge_service] normalize=auto SKIP ({reason})")

        if need_norm:
            normed = _normalize_pages_gs(merged_path, page_size=norm_page_size)
            try: os.remove(merged_path)
            except OSError: pass
            merged_path = normed

        if not flatten:
            current_app.logger.debug(f"[merge_service] PDF mesclado (normalize={mode}) sem flatten: {merged_path}")
            return merged_path

        # 6) Flatten com cache
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