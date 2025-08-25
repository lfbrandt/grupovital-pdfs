import os
import tempfile
import platform
import hashlib
import shutil
from typing import List, Optional

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
# Aceita GS_BIN (novo) e GHOSTSCRIPT_BIN (antigo)
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
    return angle % 360


def _reset_and_rotate(page, angle: int):
    # Zera rotações antigas definidas em metadata
    page[NameObject("/Rotate")] = NumberObject(0)

    if angle:
        try:
            page.rotate(angle)            # PyPDF2 >= 3
        except Exception:
            page.rotate_clockwise(angle)  # fallback

        # Ajusta mediabox se girou 90° ou 270°
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
        "-dAutoRotatePages=/None",
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


def _normalize_pages_selection(pages: Optional[List[int]], total: int) -> List[int]:
    """
    Aceita páginas 0-based (0..total-1) ou 1-based (1..total) e normaliza para 0-based.
    Se pages for None/[] → seleciona todas (0..total-1).
    """
    if not pages:
        return list(range(total))
    # Verifica se é 1-based (tudo entre 1..total e nenhum zero)
    if all(1 <= p <= total for p in pages):
        return [p - 1 for p in pages]
    # Verifica se é 0-based válido
    if all(0 <= p < total for p in pages):
        return pages
    raise BadRequest(f"Índices de página inválidos: {pages}")


def merge_selected_pdfs(
    file_paths: List,                              # FileStorage ou paths
    pages_map: Optional[List[List[int]]] = None,   # opcional (por arquivo)
    rotations_map: Optional[List[List[int]]] = None,
    flatten: bool = False,
    pdf_settings: str = "/ebook",
    auto_orient: bool = False,
    crops: Optional[List[List[dict]]] = None,
) -> str:
    """
    Junta PDFs mantendo a ORDEM de 'file_paths'.
    - pages_map: por arquivo, opcional; aceita 0-based OU 1-based (normalizado aqui).
    - rotations_map: lista de listas de ângulos (int). Faltantes = 0º.
    - crops: [{page:int, box:[x1,y1,x2,y2]}] por arquivo. 'page' pode ser 1-based.
    - flatten: se True, aplica Ghostscript ao PDF FINAL (cacheado por hash).
    Segurança: sanitiza cada entrada, aplica limites de páginas, roda GS em sandbox.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    rotations_map = rotations_map or []
    crops = crops or [[] for _ in range(len(file_paths))]

    writer = PdfWriter()
    temp_files: List[str] = []         # arquivos salvos a partir de FileStorage
    processed_inputs: List[str] = []   # sanitizados / intermediários p/ limpeza
    total_selected_pages = 0

    try:
        for idx, original in enumerate(file_paths):
            # Aceita FileStorage (salva temp) ou path
            path = original
            if hasattr(original, "save"):  # FileStorage
                tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
                original.save(tf.name)
                tf.close()
                temp_files.append(tf.name)
                path = tf.name

            # 1) Sanitização de ENTRADA (remove JS/anotações, etc.)
            sanitized = os.path.join(upload_folder, f"san_{hashlib.md5((str(path)+str(idx)).encode()).hexdigest()}.pdf")
            try:
                sanitize_pdf(path, sanitized)
                processed_inputs.append(sanitized)
                path = sanitized
            except Exception:
                # Se falhar, segue com o original (ainda assim o limite pega)
                pass

            # 2) Limite por arquivo (antes da leitura)
            enforce_pdf_page_limit(path, label=os.path.basename(path))

            # 3) Leitura
            try:
                reader = PdfReader(path)
            except PdfReadError:
                raise BadRequest(f"Arquivo inválido ou corrompido: {os.path.basename(path)}")

            total = len(reader.pages)
            # Seleção de páginas (normalizada para 0-based)
            raw_pages = (pages_map[idx] if (pages_map and idx < len(pages_map)) else None)
            page_indices = _normalize_pages_selection(raw_pages, total)

            # Limite global progressivo
            total_selected_pages += len(page_indices)
            enforce_total_pages(total_selected_pages)

            rots = rotations_map[idx] if idx < len(rotations_map) else []
            file_crops = crops[idx] if idx < len(crops) else []

            for j, pidx in enumerate(page_indices):
                page = reader.pages[pidx]

                # ► auto_orient SOMADO à rotação do usuário
                base_angle = 0
                if auto_orient:
                    w = float(page.mediabox.width)
                    h = float(page.mediabox.height)
                    if w > h:
                        base_angle = 90

                user_angle = rots[j] if j < len(rots) else 0
                angle = _normalize_angle(base_angle + user_angle)

                # Recortes que apontam para esta página (1-based compatível)
                for rec in file_crops:
                    rec_page = rec.get("page", 0)
                    # normaliza: 1-based → 0-based
                    rec_idx = rec_page - 1 if rec_page >= 1 else rec_page
                    if rec_idx == pidx:
                        x1, y1, x2, y2 = rec.get("box", [0, 0, 0, 0])
                        page.cropbox = RectangleObject((x1, y1, x2, y2))

                _reset_and_rotate(page, angle)
                writer.add_page(page)

        # 4) Escreve o PDF mesclado (sem flatten)
        out_tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
        with out_tf:
            writer.write(out_tf)
        merged_path = out_tf.name

        if not flatten:
            current_app.logger.debug(f"[merge_service] PDF mesclado sem flatten: {merged_path}")
            return merged_path

        # 5) Flatten do RESULTADO com cache por hash
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
            # retorna cópia do cache (para poder limpar após resposta)
            tmp_copy = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
            tmp_copy.close()
            shutil.copy(cache_file, tmp_copy.name)
            return tmp_copy.name

        flat_merged = _flatten_pdf(merged_path, pdf_settings)
        # guarda no cache e retorna handle limpável
        try:
            shutil.copy(flat_merged, cache_file)
        except OSError:
            current_app.logger.warning(f"Falha ao escrever cache: {cache_file}")
        finally:
            try:
                os.remove(merged_path)
            except OSError:
                current_app.logger.warning(f"Não removeu intermediário: {merged_path}")

        # Retorna o path flatten para o caller fazer cleanup pós-resposta
        return flat_merged

    finally:
        # 6) Limpeza de temporários criados
        for f in temp_files + processed_inputs:
            try:
                os.remove(f)
            except OSError:
                pass