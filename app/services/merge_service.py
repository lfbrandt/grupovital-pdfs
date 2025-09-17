# app/services/merge_service.py
# -*- coding: utf-8 -*-
import os
import tempfile
import platform
import hashlib
import shutil
from typing import List, Optional, Dict, Any

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


def _reset_and_rotate(page, angle: int):
    """
    Zera rotações antigas e aplica rotação ABSOLUTA com ajuste de mediabox
    quando 90°/270°. Isso garante que o resultado final reflita exatamente
    o ângulo desejado, sem somar com /Rotate já existente.
    """
    # Zera /Rotate existente
    page[NameObject("/Rotate")] = NumberObject(0)

    # Aplica rotação (PyPDF2 escreve /Rotate)
    if angle:
        try:
            page.rotate(angle)            # PyPDF2 >= 3
        except Exception:
            page.rotate_clockwise(angle)  # fallback

        # Ajuste de mediabox para manter layout/viewbox correto
        if angle in (90, 270):
            mb = page.mediabox
            page.mediabox = RectangleObject((0, 0, mb.height, mb.width))


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
        "-dAutoRotatePages=/None",        # 🔒 NÃO deixar GS “desvirar” páginas
        "-dDetectDuplicateImages=true",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-dSAFER",                        # ✅ hardening
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
    """Aceita 0-based (0..total-1) ou 1-based (1..total) e normaliza para 0-based."""
    if 1 <= p <= total:
        return p - 1
    if 0 <= p < total:
        return p
    raise BadRequest(f"Índice de página inválido: {p} (total={total})")


def _normalize_pages_selection(pages: Optional[List[int]], total: int) -> List[int]:
    """Legado: lista por arquivo; normaliza para 0-based; vazio = todas."""
    if not pages:
        return list(range(total))
    if all(1 <= v <= total for v in pages):  # 1-based?
        return [v - 1 for v in pages]
    if all(0 <= v < total for v in pages):   # 0-based?
        return pages
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
    Extrai page_idx do reader, aplica crop e rotação ABSOLUTA e adiciona ao writer.

    Regra anti-“soma de 90°”:
      - 'angle_abs' é ângulo final desejado no PDF resultante (0/90/180/270).
      - auto_orient só é considerado se angle_abs == 0 (usuário não pediu rotação).
    """
    page = reader.pages[page_idx]

    # auto_orient SOMENTE se o usuário não definiu ângulo (angle_abs == 0)
    base_angle = 0
    if auto_orient and (angle_abs % 360 == 0):
        try:
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            if w > h:
                base_angle = 90
        except Exception:
            base_angle = 0

    final_angle = _normalize_angle(base_angle + _normalize_angle(angle_abs))

    if crop:
        if not (isinstance(crop, list) and len(crop) == 4):
            raise BadRequest("Crop inválido; esperado [x1,y1,x2,y2].")
        x1, y1, x2, y2 = crop
        page.cropbox = RectangleObject((x1, y1, x2, y2))

    _reset_and_rotate(page, final_angle)
    writer.add_page(page)


def merge_selected_pdfs(
    file_paths: List[str],                             # paths temporários
    plan: Optional[List[Dict[str, Any]]] = None,      # flat plan (ABSOLUTO)
    pages_map: Optional[List[List[int]]] = None,      # legado
    rotations_map: Optional[List[List[int]]] = None,  # legado
    flatten: bool = False,
    pdf_settings: str = "/ebook",
    auto_orient: bool = False,
    crops: Optional[List[List[dict]]] = None,         # legado
) -> str:
    """
    Junta PDFs.

    Regras:
    - Quando 'plan' é fornecido:
        * 'rotation' é **ABSOLUTO** (0/90/180/270) e será gravado como /Rotate.
        * 'auto_orient' é IGNORADO (forçado false) para não interferir na UI.
    - Modo legado (pages_map/rotations_map/crops):
        * Mantém comportamento antigo (rotação definida por arquivo).
    - Segurança: sanitização, limites, GS -dSAFER, sem auto-rotate.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    writer = PdfWriter()
    processed_inputs: List[str] = []   # sanitizados/intermediários p/ limpeza
    total_selected_pages = 0

    # 1) Sanitiza e abre todos os readers
    readers: List[PdfReader] = []
    try:
        for idx, path in enumerate(file_paths):
            sanitized = os.path.join(upload_folder, f"san_{hashlib.md5((str(path)+str(idx)).encode()).hexdigest()}.pdf")
            try:
                sanitize_pdf(path, sanitized)
                use_path = sanitized
            except Exception:
                use_path = path  # fallback
            if use_path != path:
                processed_inputs.append(use_path)

            enforce_pdf_page_limit(use_path, label=os.path.basename(path))
            try:
                readers.append(PdfReader(use_path))
            except PdfReadError:
                raise BadRequest(f"Arquivo inválido ou corrompido: {os.path.basename(path)}")

        # 2) Caminho A: flat plan (ABSOLUTO)
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

                # ⚠️ auto_orient é ignorado quando plan está presente
                _extract_and_write_page(reader, pidx, writer,
                                        angle_abs=angle_abs, crop=crop,
                                        auto_orient=False)

        # 3) Caminho B: legado por arquivo
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

        # 4) Escreve o PDF mesclado (sem flatten)
        out_tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
        with out_tf:
            writer.write(out_tf)
        merged_path = out_tf.name

        if not flatten:
            current_app.logger.debug(f"[merge_service] PDF mesclado sem flatten: {merged_path}")
            return merged_path

        # 5) Flatten do RESULTADO com cache
        with open(merged_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        cache_dir = current_app.config.get("MERGE_CACHE_DIR", tempfile.gettempdir())
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{digest}_{pdf_settings.replace('/', '')}.pdf")

        if os.path.exists(cache_file):
            current_app.logger.debug(f"[merge_service] Cache hit: {cache_file}")
            try:
                os.remove(merged_path)
            except OSError:
                pass
            tmp_copy = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
            tmp_copy.close()
            shutil.copy(cache_file, tmp_copy.name)
            return tmp_copy.name

        flat_merged = _flatten_pdf(merged_path, pdf_settings)
        try:
            shutil.copy(flat_merged, cache_file)
        except OSError:
            current_app.logger.warning(f"Falha ao escrever cache: {cache_file}")
        finally:
            try:
                os.remove(merged_path)
            except OSError:
                current_app.logger.warning(f"Não removeu intermediário: {merged_path}")

        return flat_merged

    finally:
        for p in processed_inputs:
            try:
                os.remove(p)
            except OSError:
                pass