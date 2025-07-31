import os
import tempfile
import subprocess
import platform
import hashlib
import shutil
from flask import current_app
from werkzeug.exceptions import BadRequest
from PyPDF2 import PdfReader, PdfWriter, Transformation
from PyPDF2.errors import PdfReadError
from PyPDF2.generic import NameObject, NumberObject

# Ghostscript configuration (flatten)
_env_gs = os.environ.get("GHOSTSCRIPT_BIN")
if _env_gs:
    GHOSTSCRIPT_BIN = _env_gs
elif platform.system() == "Windows":
    GHOSTSCRIPT_BIN = "gswin64c"
else:
    GHOSTSCRIPT_BIN = "gs"
GHOSTSCRIPT_TIMEOUT = int(os.environ.get("GHOSTSCRIPT_TIMEOUT", "60"))


def _normalize_angle(angle: int) -> int:
    return angle % 360


def _reset_and_rotate(page, angle: int):
    # Remove qualquer /Rotate deixado no metadata
    orig = _normalize_angle(page.get(NameObject("/Rotate"), 0))
    if orig:
        page.add_transformation(Transformation().rotate(-orig))
        page[NameObject("/Rotate")] = NumberObject(0)

    # Aplica rotação ao conteúdo
    if angle:
        page.add_transformation(Transformation().rotate(angle))
        if angle in (90, 270):
            mb = page.mediabox
            mb.upper_right = (mb.height, mb.width)


def _flatten_pdf(input_path: str, pdf_settings: str) -> str:
    dirname, filename = os.path.split(input_path)
    flat_name = filename.replace(".pdf", f"_flat_{pdf_settings.replace('/', '')}.pdf")
    flat_path = os.path.join(dirname, flat_name)
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
    flatten: bool = True,
    pdf_settings: str = "/ebook"
) -> str:
    """
    Combina páginas de múltiplos PDFs:
      - file_paths: caminhos dos PDFs de entrada
      - pages_map: listas de índices 1-based por arquivo
      - rotations_map: listas de ângulos (0,90,180,270) por arquivo
      - flatten: se True, pré-flattena cada input **e** o merged final
      - pdf_settings: '/screen', '/ebook' ou '/prepress'
    """
    rotations_map = rotations_map or []
    writer = PdfWriter()

    # 1) Pré-flatten cada PDF de entrada, se solicitado
    processed_inputs = []
    for idx, original_path in enumerate(file_paths):
        path = original_path
        if flatten:
            path = _flatten_pdf(path, pdf_settings)
            processed_inputs.append(path)

        try:
            reader = PdfReader(path)
        except PdfReadError:
            raise BadRequest(f"Arquivo inválido ou corrompido: {os.path.basename(path)}")

        total = len(reader.pages)
        raw = pages_map[idx] if idx < len(pages_map) else None
        pages = list(range(1, total + 1)) if not raw else raw
        rots = rotations_map[idx] if idx < len(rotations_map) else []

        invalid = [p for p in pages if p < 1 or p > total]
        if invalid:
            raise BadRequest(f"Páginas inválidas em {os.path.basename(path)}: {invalid}")

        for seq, pnum in enumerate(pages, start=1):
            page = reader.pages[pnum - 1]
            angle = rots[seq - 1] if seq - 1 < len(rots) else 0
            _reset_and_rotate(page, angle)
            writer.add_page(page)

    # 2) Escreve o PDF mesclado
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as out_tf:
        writer.write(out_tf)
    merged_path = out_tf.name

    # 3) Se não quiser flatten final, retorna agora
    if not flatten:
        current_app.logger.debug(f"[merge_service] PDF mesclado (sem flatten): {merged_path}")
        return merged_path

    # 4) Flatten final + cache
    with open(merged_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    cache_dir = current_app.config.get("MERGE_CACHE_DIR", tempfile.gettempdir())
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{digest}_{pdf_settings.replace('/', '')}.pdf")

    if os.path.exists(cache_file):
        current_app.logger.debug(f"[merge_service] Usando cache Ghostscript: {cache_file}")
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
            current_app.logger.warning(f"Não foi possível remover intermédio: {merged_path}")

    current_app.logger.debug(f"[merge_service] PDF gerado e flattened em: {flat_merged}")
    return flat_merged