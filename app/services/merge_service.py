# app/services/merge_service.py
import os
import tempfile
import subprocess
import platform
import hashlib
import shutil
from flask import current_app
from werkzeug.exceptions import BadRequest
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.errors import PdfReadError
from PyPDF2.generic import NameObject, NumberObject, RectangleObject
from ..utils.config_utils import ensure_upload_folder_exists

# Ghostscript configuration
env_gs = os.environ.get("GHOSTSCRIPT_BIN")
if env_gs:
    GHOSTSCRIPT_BIN = env_gs
elif platform.system() == "Windows":
    GHOSTSCRIPT_BIN = "gswin64c"
else:
    GHOSTSCRIPT_BIN = "gs"
GHOSTSCRIPT_TIMEOUT = int(os.environ.get("GHOSTSCRIPT_TIMEOUT", "60"))

def _normalize_angle(angle: int) -> int:
    return angle % 360

def _reset_and_rotate(page, angle: int):
    # Zera rotações antigas definidas em metadata
    page[NameObject("/Rotate")] = NumberObject(0)

    if angle:
        # Use rotate() para compatibilidade com PyPDF2>=3.0.0
        try:
            page.rotate(angle)
        except Exception:
            # fallback para versões antigas
            page.rotate_clockwise(angle)

        # Ajusta mediabox se girou 90° ou 270°
        if angle in (90, 270):
            mb = page.mediabox
            page.mediabox = RectangleObject((0, 0, mb.height, mb.width))

def _flatten_pdf(input_path: str, pdf_settings: str) -> str:
    dirname, filename = os.path.split(input_path)
    flat_name = filename.replace(".pdf", f"_flat_{pdf_settings.replace('/', '')}.pdf")
    flat_path = os.path.join(dirname, flat_name)
    if shutil.which(GHOSTSCRIPT_BIN) is None:
        raise BadRequest("Ghostscript não encontrado para flatten.")

    cmd = [
        GHOSTSCRIPT_BIN,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={pdf_settings}",
        "-dAutoRotatePages=/None",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        f"-sOutputFile={flat_path}",
        input_path,
    ]
    current_app.logger.debug(f"[merge_service] Ghostscript cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=GHOSTSCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BadRequest("Ghostscript expirou ao tentar flattenar o PDF.")
    if proc.returncode != 0:
        raise BadRequest(f"Falha ao flattenar PDF: {proc.stderr.strip()}")
    return flat_path

def merge_selected_pdfs(
    file_paths,
    pages_map,
    rotations_map=None,
    flatten: bool = False,
    pdf_settings: str = "/ebook",
    auto_orient: bool = False,
    crops=None
) -> str:
    """
    IMPORTANTE: a ordem do resultado segue EXATAMENTE a ordem de 'file_paths'
    e, para cada arquivo, a ordem da lista 'pages_map[idx]'.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)
    rotations_map = rotations_map or []
    crops = crops or [[] for _ in file_paths]
    writer = PdfWriter()
    temp_files = []
    processed_inputs = []

    try:
        for idx, original in enumerate(file_paths):
            path = original
            if hasattr(original, "save"):
                tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
                original.save(tf.name)
                tf.close()
                temp_files.append(tf.name)
                path = tf.name

            if flatten:
                flat_path = _flatten_pdf(path, pdf_settings)
                processed_inputs.append(flat_path)
                path = flat_path

            try:
                reader = PdfReader(path)
            except PdfReadError:
                raise BadRequest(f"Arquivo inválido ou corrompido: {os.path.basename(path)}")

            total = len(reader.pages)
            pages = pages_map[idx] if idx < len(pages_map) else list(range(1, total + 1))
            invalid = [p for p in pages if p < 1 or p > total]
            if invalid:
                raise BadRequest(f"Páginas inválidas em {os.path.basename(path)}: {invalid}")

            rots = rotations_map[idx] if idx < len(rotations_map) else []
            file_crops = crops[idx] if idx < len(crops) else []

            for seq, pnum in enumerate(pages, start=1):
                page = reader.pages[pnum - 1]

                # ► auto_orient SOMADO à rotação do usuário
                base_angle = 0
                if auto_orient:
                    w = float(page.mediabox.width)
                    h = float(page.mediabox.height)
                    if w > h:
                        base_angle = 90

                user_angle = rots[seq - 1] if seq - 1 < len(rots) else 0
                angle = _normalize_angle(base_angle + user_angle)

                for rec in file_crops:
                    if rec.get("page", 0) - 1 == pnum - 1:
                        x1, y1, x2, y2 = rec.get("box", [0, 0, 0, 0])
                        page.cropbox = RectangleObject((x1, y1, x2, y2))

                _reset_and_rotate(page, angle)
                writer.add_page(page)

        out_tf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=upload_folder)
        with out_tf:
            writer.write(out_tf)
        merged_path = out_tf.name

        if not flatten:
            current_app.logger.debug(f"[merge_service] PDF mesclado sem flatten: {merged_path}")
            return merged_path

        with open(merged_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        cache_dir = current_app.config.get("MERGE_CACHE_DIR", tempfile.gettempdir())
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{digest}_{pdf_settings.replace('/', '')}.pdf")

        if os.path.exists(cache_file):
            current_app.logger.debug(f"[merge_service] Cache hit: {cache_file}")
            os.remove(merged_path)
            return cache_file

        flat_merged = _flatten_pdf(merged_path, pdf_settings)
        try:
            shutil.copy(flat_merged, cache_file)
        except OSError:
            current_app.logger.warning(f"Falha ao escrever cache: {cache_file}")
        finally:
            try:
                os.remove(merged_path)
            except OSError:
                current_app.logger.warning(f"Não removeu intermédio: {merged_path}")

        return flat_merged

    finally:
        for f in temp_files + processed_inputs:
            try:
                os.remove(f)
            except OSError:
                pass