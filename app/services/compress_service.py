import os
import re
import uuid
import platform
from glob import glob
from flask import current_app
from werkzeug.exceptions import BadRequest
from PyPDF2 import PdfReader, PdfWriter

from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications
from ..utils.limits import (
    enforce_pdf_page_limit,
    enforce_total_pages,
)
from .sandbox import run_in_sandbox  # ✅ sandbox seguro
from .sanitize_service import sanitize_pdf  # ✅ sanitização de PDFs

# =========================
# Configurações
# =========================
# Aceita novos e antigos nomes de env (GS_TIMEOUT ↔ GHOSTSCRIPT_TIMEOUT)
_GS_TO = os.environ.get("GS_TIMEOUT") or os.environ.get("GHOSTSCRIPT_TIMEOUT") or "120"
GHOSTSCRIPT_TIMEOUT = int(_GS_TO)
QPDF_TIMEOUT        = int(os.environ.get("QPDF_TIMEOUT", "90"))

# Perfis internos (mantidos para GS)
PROFILES = {
    "screen":  ["-dPDFSETTINGS=/screen",  "-dColorImageResolution=72"],
    "ebook":   ["-dPDFSETTINGS=/ebook",   "-dColorImageResolution=150"],
    "printer": ["-dPDFSETTINGS=/printer", "-dColorImageResolution=300"],
    "lossless": []
}

# Perfis expostos ao usuário (nomes PT-BR + descrições)
USER_PROFILES = {
    "mais-leve": {
        "internal": "screen",
        "label": "Arquivo menor (web/e-mail)",
        "hint":  "Máxima redução. Pode baixar a qualidade de imagens (≈72–96 dpi). Ideal para envio rápido."
    },
    "equilibrio": {
        "internal": "ebook",
        "label": "Equilíbrio (recomendado)",
        "hint":  "Boa qualidade em tela com tamanho reduzido (≈150 dpi). Melhor custo/benefício."
    },
    "alta-qualidade": {
        "internal": "printer",
        "label": "Alta qualidade (impressão)",
        "hint":  "Preserva detalhes (≈300 dpi). Arquivo maior. Bom para impressão."
    },
    "sem-perdas": {
        "internal": "lossless",
        "label": "Sem perdas (seguro)",
        "hint":  "Não reamostra imagens; apenas otimiza fluxos. Usa fallback seguro."
    }
}

# Compatibilidade com nomes antigos
PROFILE_ALIASES = {
    "screen": "mais-leve",
    "ebook": "equilibrio",
    "printer": "alta-qualidade",
    "lossless": "sem-perdas"
}

def resolve_profile(name: str) -> str:
    n = (name or "").strip().lower()
    if n in USER_PROFILES:
        return USER_PROFILES[n]["internal"]
    if n in PROFILE_ALIASES:
        return USER_PROFILES[PROFILE_ALIASES[n]]["internal"]
    return USER_PROFILES["equilibrio"]["internal"]  # default

# =========================
# Localização de binários
# =========================
def _locate_windows_ghostscript():
    patterns = [
        r"C:\Program Files\gs\*\bin\gswin64c.exe",
        r"C:\Program Files (x86)\gs\*\bin\gswin64c.exe",
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob(pat))
    if not candidates:
        return None
    def version_key(path):
        m = re.search(r"gs(\d+(?:\.\d+)*)", path)
        return [int(x) for x in m.group(1).split('.')] if m else [0]
    return max(candidates, key=version_key)

def _locate_windows_qpdf():
    patterns = [
        r"C:\Program Files\qpdf\bin\qpdf.exe",
        r"C:\Program Files (x86)\qpdf\bin\qpdf.exe",
    ]
    for pat in patterns:
        hits = glob(pat)
        if hits:
            return hits[0]
    return None

def _get_ghostscript_cmd():
    # ✅ aceita GS_BIN (novo) e GHOSTSCRIPT_BIN (antigo)
    gs = os.environ.get("GS_BIN") or os.environ.get("GHOSTSCRIPT_BIN")
    if not gs and platform.system() == "Windows":
        gs = _locate_windows_ghostscript()
    return gs or "gs"

def _get_qpdf_cmd():
    q = os.environ.get("QPDF_BIN")
    if not q and platform.system() == "Windows":
        q = _locate_windows_qpdf()
    return q or "qpdf"

# =========================
# Helpers
# =========================
def _page_count(path: str) -> int:
    with open(path, "rb") as f:
        return len(PdfReader(f).pages)

def _run(cmd, timeout, *, cpu_seconds=60, mem_mb=768):
    """Executa comando externo no sandbox (limites de CPU/RAM/tempo)."""
    current_app.logger.debug("exec: %s", " ".join(map(str, cmd)))
    run_in_sandbox(
        cmd,
        timeout=timeout,
        cpu_seconds=cpu_seconds,
        mem_mb=mem_mb,
    )

def _qpdf_flatten(src: str, dst: str):
    qpdf = _get_qpdf_cmd()
    _run([qpdf, "--silent",
          "--flatten-annotations=all",
          "--object-streams=generate",
          "--stream-data=compress",
          src, dst], timeout=QPDF_TIMEOUT, cpu_seconds=45, mem_mb=512)

def _qpdf_optimize_lossless(src: str, dst: str):
    qpdf = _get_qpdf_cmd()
    _run([qpdf, "--silent",
          "--object-streams=generate",
          "--stream-data=compress",
          src, dst], timeout=QPDF_TIMEOUT, cpu_seconds=45, mem_mb=512)

def _run_ghostscript(input_pdf: str, output_pdf: str, profile_internal: str):
    gs_cmd = _get_ghostscript_cmd()
    gs_args = [
        gs_cmd,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        "-dDetectDuplicateImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dMonoImageDownsampleType=/Subsample",
        "-dShowAnnots=true",   # garante appearances visíveis
        "-dSAFER",             # ✅ hardening essencial
        "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={output_pdf}",
    ]
    gs_args += PROFILES.get(profile_internal, PROFILES["ebook"])
    gs_args.append(input_pdf)
    _run(gs_args, timeout=GHOSTSCRIPT_TIMEOUT, cpu_seconds=60, mem_mb=768)

# =========================
# Serviço principal
# =========================
def comprimir_pdf(file, pages=None, rotations=None, modificacoes=None, profile: str = "equilibrio"):
    """
    Comprime um PDF aplicando (opcionalmente) seleção/ordem de páginas (DnD),
    rotações e outras modificações ANTES da compressão.

    - Sanitiza PDF (remove JS/anotações/anexos) imediatamente após upload.
    - Flatten com QPDF para preservar bordas/linhas/camadas.
    - Compressão via Ghostscript com perfis.
    - Fallback seguro (lossless) se não houver ganho ou se houver risco de perda de páginas.

    Retorna o caminho do PDF final dentro de UPLOAD_FOLDER.
    """
    ensure_upload_folder_exists(current_app.config['UPLOAD_FOLDER'])
    upload_folder = current_app.config['UPLOAD_FOLDER']
    cleanup = []

    # 1) Validar e salvar input
    filename = validate_upload(file, {'pdf'})
    basename, _ = os.path.splitext(filename)
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)

    # 2) Sanitização imediata do PDF
    clean_path = os.path.join(upload_folder, f"clean_{uuid.uuid4().hex}.pdf")
    try:
        sanitize_pdf(input_path, clean_path)
        base_source = clean_path
        cleanup.append(clean_path)
    except Exception:
        # Se a sanitização falhar, segue com o original
        base_source = input_path

    # 2.1) Limite por arquivo (se não houver filtro de páginas)
    if not pages:
        enforce_pdf_page_limit(base_source, label="PDF de entrada")

    # 3) Modificações genéricas baseadas em arquivo
    if modificacoes:
        try:
            apply_pdf_modifications(base_source, modificacoes)
        except TypeError:
            # Implementações que só aceitam Page serão tratadas no laço abaixo
            pass

    # 4) Seleção/ordem e rotações — gera uma fonte temporária já no layout desejado
    temp_source = base_source
    if pages or rotations:
        reader = PdfReader(base_source)
        total = len(reader.pages)

        # normalizar páginas
        if pages:
            pages_to_emit = [int(p) for p in pages if 1 <= int(p) <= total]
            if not pages_to_emit:
                pages_to_emit = list(range(1, total + 1))
        else:
            pages_to_emit = list(range(1, total + 1))

        # Limite por quantidade selecionada (quando 'pages' é usado)
        if pages:
            enforce_total_pages(len(pages_to_emit))

        # normalizar rotações
        rots_map = {}
        if isinstance(rotations, dict):
            for k, v in rotations.items():
                try:
                    k_int = int(k)
                except Exception:
                    continue
                page_num = k_int + 1 if 0 <= k_int <= total - 1 else k_int
                if 1 <= page_num <= total:
                    rots_map[page_num] = int(v)
        elif isinstance(rotations, list):
            if pages:
                for idx, ang in enumerate(rotations):
                    if idx < len(pages_to_emit):
                        rots_map[pages_to_emit[idx]] = int(ang)
            else:
                for i, ang in enumerate(rotations):
                    if i < total:
                        rots_map[i + 1] = int(ang)

        writer = PdfWriter()
        for p in pages_to_emit:
            page = reader.pages[p - 1]
            angle = int(rots_map.get(p, 0) or 0)
            if angle:
                try:
                    page.rotate(angle)
                except Exception:
                    page.rotate_clockwise(angle)

            if modificacoes:
                try:
                    apply_pdf_modifications(page, modificacoes=modificacoes)
                except TypeError:
                    pass

            writer.add_page(page)

        ordered_path = os.path.join(upload_folder, f"ordered_{uuid.uuid4().hex}.pdf")
        with open(ordered_path, "wb") as out_f:
            writer.write(out_f)
        temp_source = ordered_path
        cleanup.append(ordered_path)

    # 5) Flatten com QPDF (evita “linhas sumindo” após GS)
    flat_path = os.path.join(upload_folder, f"flat_{uuid.uuid4().hex}.pdf")
    try:
        _qpdf_flatten(temp_source, flat_path)
        stage_source = flat_path
        cleanup.append(flat_path)
    except Exception:
        stage_source = temp_source  # segue mesmo sem flatten

    original_pages = _page_count(stage_source)
    original_size  = os.path.getsize(stage_source)

    # 6) Perfil interno
    internal_profile = resolve_profile(profile)

    # 7) Lossless direto (sem GS)
    if internal_profile == "lossless":
        out_lossless = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
        _qpdf_optimize_lossless(stage_source, out_lossless)
        # limpeza
        try: os.remove(input_path)
        except OSError: pass
        for p in cleanup:
            try: os.remove(p)
            except OSError: pass
        return out_lossless

    # 8) Ghostscript com checagens de integridade/ganho
    out_gs = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
    try:
        _run_ghostscript(stage_source, out_gs, profile_internal=internal_profile)
        pages_after = _page_count(out_gs)
        size_after  = os.path.getsize(out_gs)

        # Se perdeu páginas ou ganho < 2%, gera versão segura (lossless)
        if pages_after != original_pages or size_after >= original_size * 0.98:
            safe_out = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
            _qpdf_optimize_lossless(stage_source, safe_out)
            return safe_out

        return out_gs

    except Exception:
        # Falha no GS: retorna otimização sem perdas
        safe_out = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
        _qpdf_optimize_lossless(stage_source, safe_out)
        return safe_out

    finally:
        # Limpeza de intermediários (não remove o arquivo final!)
        try: os.remove(input_path)
        except OSError: pass
        for p in cleanup:
            try: os.remove(p)
            except OSError: pass