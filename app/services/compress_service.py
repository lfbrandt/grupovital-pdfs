# app/services/compress_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import uuid
import platform
import shutil
from glob import glob
from pathlib import Path
from typing import Optional, Dict, List

from flask import current_app
from PyPDF2 import PdfReader
import pikepdf  # robusto para leitura/escrita/rotação

from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications
from ..utils.limits import enforce_pdf_page_limit, enforce_total_pages
from .sandbox import run_in_sandbox               # sandbox seguro (-dSAFER, limites)
from .sanitize_service import sanitize_pdf        # remove JS/anotações

# =========================
# Configurações / Perfis
# =========================
_GS_TO = os.environ.get("GS_TIMEOUT") or os.environ.get("GHOSTSCRIPT_TIMEOUT") or "120"
GHOSTSCRIPT_TIMEOUT = int(_GS_TO)
QPDF_TIMEOUT        = int(os.environ.get("QPDF_TIMEOUT", "90"))

PROFILES = {
    "screen":  ["-dPDFSETTINGS=/screen",  "-dColorImageResolution=72"],
    "ebook":   ["-dPDFSETTINGS=/ebook",   "-dColorImageResolution=150"],
    "printer": ["-dPDFSETTINGS=/printer", "-dColorImageResolution=300"],
    "lossless": []
}

USER_PROFILES: Dict[str, Dict[str, str]] = {
    "mais-leve":      {"internal": "screen",  "label": "Arquivo menor (web/e-mail)",     "hint": "Máxima redução (≈72–96 dpi)."},
    "equilibrio":     {"internal": "ebook",   "label": "Equilíbrio (recomendado)",       "hint": "Boa qualidade em tela (≈150 dpi)."},
    "alta-qualidade": {"internal": "printer", "label": "Alta qualidade (impressão)",     "hint": "Preserva detalhes (≈300 dpi)."},
    "sem-perdas":     {"internal": "lossless","label": "Sem perdas (seguro)",            "hint": "Não reamostra; só otimiza fluxos."},
}
PROFILE_ALIASES = {"screen":"mais-leve","ebook":"equilibrio","printer":"alta-qualidade","lossless":"sem-perdas"}

def resolve_profile(name: str) -> str:
    n = (name or "").strip().lower()
    if n in USER_PROFILES: return USER_PROFILES[n]["internal"]
    if n in PROFILE_ALIASES: return USER_PROFILES[PROFILE_ALIASES[n]]["internal"]
    return USER_PROFILES["equilibrio"]["internal"]  # default

# =========================
# Localização de binários
# =========================
def _locate_windows_ghostscript():
    patterns = [r"C:\Program Files\gs\*\bin\gswin64c.exe", r"C:\Program Files (x86)\gs\*\bin\gswin64c.exe"]
    hits: List[str] = []
    for pat in patterns: hits.extend(glob(pat))
    if not hits: return None
    def vkey(p: str):
        m = re.search(r"gs(\d+(?:\.\d+)*)", p)
        return [int(x) for x in m.group(1).split('.')] if m else [0]
    return max(hits, key=vkey)

def _locate_windows_qpdf():
    for pat in (r"C:\Program Files\qpdf\bin\qpdf.exe", r"C:\Program Files (x86)\qpdf\bin\qpdf.exe"):
        found = glob(pat)
        if found: return found[0]
    return None

def _invalid_for_linux(path: str) -> bool:
    if not path: return False
    p = path.lower()
    return ('\\' in path) or (':\\' in p) or ('gswin' in p)

def _get_ghostscript_cmd() -> str:
    gs_env = os.environ.get("GS_BIN") or os.environ.get("GHOSTSCRIPT_BIN")
    if platform.system() == "Windows":
        return gs_env or _locate_windows_ghostscript() or "gswin64c.exe"
    # Linux/Unix
    if gs_env and not _invalid_for_linux(gs_env):
        return gs_env
    return "/usr/bin/gs" if Path("/usr/bin/gs").exists() else "gs"

_QPDF_BIN_CACHE: Optional[str] = None
def _get_qpdf_cmd() -> Optional[str]:
    global _QPDF_BIN_CACHE
    if _QPDF_BIN_CACHE is not None: return _QPDF_BIN_CACHE
    q = os.environ.get("QPDF_BIN")
    if not q and platform.system() == "Windows": q = _locate_windows_qpdf()
    if q and os.path.isfile(q):
        _QPDF_BIN_CACHE = q
    else:
        _QPDF_BIN_CACHE = "/usr/bin/qpdf" if platform.system() != "Windows" and Path("/usr/bin/qpdf").exists() else ("qpdf" if platform.system() != "Windows" else None)
    return _QPDF_BIN_CACHE

# =========================
# Execução / Helpers
# =========================
def _posix(p: str | Path) -> str:
    return Path(p).resolve().as_posix()

def _run(cmd, timeout, *, cpu_seconds=60, mem_mb=768):
    current_app.logger.debug("exec: %s", " ".join(map(str, cmd)))
    return run_in_sandbox(cmd, timeout=timeout, cpu_seconds=cpu_seconds, mem_mb=mem_mb)

def _page_count(path: str) -> int:
    try:
        if not os.path.exists(path):
            current_app.logger.warning("[compress] _page_count: arquivo ausente: %s", path)
            return 0
        with open(path, "rb") as f:
            return len(PdfReader(f).pages)
    except Exception as e:
        current_app.logger.warning("[compress] _page_count falhou: %s", e)
        return 0

def _ensure_dst_exists_or_copy(src: str, dst: str):
    try:
        if (not os.path.exists(dst)) or os.path.getsize(dst) == 0:
            shutil.copyfile(src, dst)
    except Exception as e2:
        current_app.logger.error("fallback copy falhou (%s); tentando pikepdf.", e2)
        with pikepdf.open(src) as pdf:
            pdf.save(dst)

def _qpdf_flatten(src: str, dst: str):
    qpdf = _get_qpdf_cmd()
    if not qpdf:
        current_app.logger.warning("qpdf não encontrado — flatten: fallback copiar.")
        _ensure_dst_exists_or_copy(src, dst); return
    try:
        _run([qpdf, "--silent","--flatten-annotations=all","--object-streams=generate","--stream-data=compress",
              _posix(src), _posix(dst)], timeout=QPDF_TIMEOUT, cpu_seconds=45, mem_mb=512)
    except Exception as e:
        current_app.logger.warning("qpdf flatten falhou (%s) — aplicando fallback.", e)
    finally:
        _ensure_dst_exists_or_copy(src, dst)

def _qpdf_optimize_lossless(src: str, dst: str):
    qpdf = _get_qpdf_cmd()
    if not qpdf:
        current_app.logger.warning("qpdf não encontrado — lossless: fallback copiar.")
        _ensure_dst_exists_or_copy(src, dst); return
    try:
        _run([qpdf, "--silent","--object-streams=generate","--stream-data=compress",
              _posix(src), _posix(dst)], timeout=QPDF_TIMEOUT, cpu_seconds=45, mem_mb=512)
    except Exception as e:
        current_app.logger.warning("qpdf lossless falhou (%s) — aplicando fallback.", e)
    finally:
        _ensure_dst_exists_or_copy(src, dst)

def _run_ghostscript(input_pdf: str, output_pdf: str, profile_internal: str):
    """
    Chama Ghostscript e valida a saída. **Sem -dQUIET** para registrar erros.
    Lança RuntimeError se não gerar um PDF válido.
    """
    gs_cmd = _get_ghostscript_cmd()
    gs_args = [
        gs_cmd,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        "-dDetectDuplicateImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dMonoImageDownsampleType=/Subsample",
        "-dShowAnnots=true",
        "-dSAFER",
        "-dNOPAUSE",
        "-dBATCH",                 # <- removido -dQUIET
        f"-sOutputFile={_posix(output_pdf)}",
    ]
    gs_args += PROFILES.get(profile_internal, PROFILES["ebook"])
    gs_args.append(_posix(input_pdf))
    _run(gs_args, timeout=GHOSTSCRIPT_TIMEOUT, cpu_seconds=60, mem_mb=768)

    # valida saída
    if (not os.path.exists(output_pdf)) or os.path.getsize(output_pdf) == 0:
        current_app.logger.error("[compress] Ghostscript não gerou saída: %s", output_pdf)
        raise RuntimeError("Ghostscript falhou")

# =========================
# Rotação/ordem via pikepdf
# =========================
def _apply_rotations_pikepdf(src_pdf: str, pages: list[int] | None, rotations: dict[int, int] | None, out_pdf: str):
    with pikepdf.open(src_pdf) as pdf_src, pikepdf.Pdf.new() as pdf_dst:
        total = len(pdf_src.pages)
        order = pages if pages else list(range(1, total + 1))
        rot_map = rotations or {}
        for p1 in order:
            if not (1 <= p1 <= total): continue
            page = pdf_src.pages[p1 - 1]
            try: base = int(page.get("/Rotate", 0)) % 360
            except Exception: base = 0
            extra = int(rot_map.get(p1, 0) or 0) % 360
            new = (base + extra) % 360
            if new == 0:
                try: del page["/Rotate"]
                except Exception: pass
            else:
                page.Rotate = new
            pdf_dst.pages.append(page)
        pdf_dst.save(out_pdf)

# =========================
# Serviço principal
# =========================
def comprimir_pdf(file, pages=None, rotations=None, modificacoes=None, profile: str = "equilibrio"):
    """
    Seleciona/ordena/rotaciona e comprime com Ghostscript.
    Se GS falhar ou não reduzir, cai no caminho lossless (qpdf).
    """
    ensure_upload_folder_exists(current_app.config['UPLOAD_FOLDER'])
    upload_folder = current_app.config['UPLOAD_FOLDER']
    cleanup: List[str] = []

    # 1) Validar e salvar input (MIME real)
    filename = validate_upload(file, {'pdf'})
    basename, _ = os.path.splitext(filename)
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)

    # 2) Sanitização imediata
    clean_path = os.path.join(upload_folder, f"clean_{uuid.uuid4().hex}.pdf")
    try:
        sanitize_pdf(input_path, clean_path)
        base_source = clean_path; cleanup.append(clean_path)
    except Exception:
        base_source = input_path

    # 2.1) Limite por arquivo
    if not pages:
        enforce_pdf_page_limit(base_source, label="PDF de entrada")

    # 3) Modificações (opcional)
    if modificacoes:
        try: apply_pdf_modifications(base_source, modificacoes=modificacoes)
        except TypeError: pass

    # 4) Seleção/ordem + rotação
    stage_source = base_source
    if pages or rotations:
        ordered_path = os.path.join(upload_folder, f"ordered_{uuid.uuid4().hex}.pdf")
        _apply_rotations_pikepdf(base_source, pages, rotations, ordered_path)
        stage_source = ordered_path; cleanup.append(ordered_path)
        if pages: enforce_total_pages(len(pages if isinstance(pages, list) else []))

    # 5) Flatten com qpdf — **sempre** cria arquivo
    flat_path = os.path.join(upload_folder, f"flat_{uuid.uuid4().hex}.pdf")
    _qpdf_flatten(stage_source, flat_path)
    stage_source = flat_path; cleanup.append(flat_path)

    original_pages = _page_count(stage_source)
    original_size  = os.path.getsize(stage_source) if os.path.exists(stage_source) else 0

    # 6) Perfil
    internal_profile = resolve_profile(profile)

    # 7) Lossless direto
    if internal_profile == "lossless":
        out_lossless = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
        _qpdf_optimize_lossless(stage_source, out_lossless)
        try: os.remove(input_path)
        except OSError: pass
        for p in cleanup:
            try: os.remove(p)
            except OSError: pass
        return out_lossless

    # 8) Ghostscript com checagens
    out_gs = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
    try:
        _run_ghostscript(stage_source, out_gs, profile_internal=internal_profile)
        pages_after = _page_count(out_gs)
        size_after  = os.path.getsize(out_gs) if os.path.exists(out_gs) else 0

        # se perdeu páginas ou não reduziu ~nada, troca para lossless “seguro”
        if (original_pages and pages_after and pages_after != original_pages) or \
           (original_size and size_after >= original_size * 0.98):
            safe_out = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
            _qpdf_optimize_lossless(stage_source, safe_out)
            return safe_out

        return out_gs

    except Exception:
        # Ghostscript falhou → retorna lossless mesmo assim
        safe_out = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
        _qpdf_optimize_lossless(stage_source, safe_out)
        return safe_out

    finally:
        try: os.remove(input_path)
        except OSError: pass
        for p in cleanup:
            try: os.remove(p)
            except OSError: pass