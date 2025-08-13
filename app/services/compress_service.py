import os
import re
import uuid
import platform
import subprocess
from glob import glob
from flask import current_app
from PyPDF2 import PdfReader, PdfWriter
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import apply_pdf_modifications

# =========================
# Configurações
# =========================
GHOSTSCRIPT_TIMEOUT = int(os.environ.get("GHOSTSCRIPT_TIMEOUT", "120"))
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
    gs = os.environ.get("GHOSTSCRIPT_BIN")
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

def _run(cmd, timeout):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=timeout)

def _qpdf_flatten(src: str, dst: str):
    # Achata anotações/aparências e reorganiza fluxos (evita “linhas sumindo”)
    qpdf = _get_qpdf_cmd()
    _run([qpdf, "--silent",
          "--flatten-annotations=all",
          "--object-streams=generate",
          "--stream-data=compress",
          src, dst], timeout=QPDF_TIMEOUT)

def _qpdf_optimize_lossless(src: str, dst: str):
    qpdf = _get_qpdf_cmd()
    _run([qpdf, "--silent",
          "--object-streams=generate",
          "--stream-data=compress",
          src, dst], timeout=QPDF_TIMEOUT)

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
        "-dSAFER",
        "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={output_pdf}",
    ]
    gs_args += PROFILES.get(profile_internal, PROFILES["ebook"])
    gs_args.append(input_pdf)
    _run(gs_args, timeout=GHOSTSCRIPT_TIMEOUT)

# =========================
# Serviço principal
# =========================
def comprimir_pdf(file, pages=None, rotations=None, modificacoes=None, profile: str = "equilibrio"):
    """
    Comprime um PDF aplicando (opcionalmente) seleção/ordem de páginas (DnD),
    rotações e outras modificações ANTES da compressão.

    - Flatten com QPDF para preservar bordas/linhas/camadas.
    - Compressão via Ghostscript com perfis.
    - Fallback seguro (lossless) se não houver ganho ou se houver risco de perda de páginas.

    Retorna o caminho do PDF final dentro de UPLOAD_FOLDER.
    """
    upload_folder = current_app.config['UPLOAD_FOLDER']
    ensure_upload_folder_exists(upload_folder)

    # 1) Validar e salvar input
    filename = validate_upload(file, {'pdf'})
    basename = os.path.splitext(filename)[0]
    unique_input = f"{uuid.uuid4().hex}_{filename}"
    input_path = os.path.join(upload_folder, unique_input)
    file.save(input_path)

    # 2) Modificações genéricas baseadas em arquivo (ex.: crop por faixa, etc.)
    #    (Se o seu apply_pdf_modifications aceitar Page, também aplicaremos por página na etapa 3.)
    if modificacoes:
        try:
            apply_pdf_modifications(input_path, modificacoes)
        except TypeError:
            # Implementações que só aceitam Page serão tratadas no laço abaixo
            pass

    # 3) Seleção/ordem e rotações — gera uma fonte temporária já no layout desejado
    temp_source = input_path
    if pages or rotations:
        reader = PdfReader(input_path)
        total = len(reader.pages)

        # normalizar páginas
        if pages:
            pages_to_emit = [int(p) for p in pages if 1 <= int(p) <= total]
            if not pages_to_emit:
                pages_to_emit = list(range(1, total + 1))
        else:
            pages_to_emit = list(range(1, total + 1))

        # normalizar rotações
        # Se vier dict, aceitamos tanto 0-based quanto 1-based (string/int).
        rots_map = {}
        if isinstance(rotations, dict):
            for k, v in rotations.items():
                try:
                    k_int = int(k)
                except Exception:
                    continue
                # aceita 0-based e 1-based
                page_num = k_int + 1 if 0 <= k_int <= total - 1 else k_int
                if 1 <= page_num <= total:
                    rots_map[page_num] = int(v)
        elif isinstance(rotations, list):
            if pages:
                # rotação alinhada à lista "pages"
                for idx, ang in enumerate(rotations):
                    if idx < len(pages_to_emit):
                        rots_map[pages_to_emit[idx]] = int(ang)
            else:
                # lista indexada pelo documento original
                for i, ang in enumerate(rotations):
                    if i < total:
                        rots_map[i + 1] = int(ang)

        # recompor em nova ordem, aplicando rotação e (se necessário) modificações por página
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
                    # algumas implementações aceitam modificar Page diretamente
                    apply_pdf_modifications(page, modificacoes=modificacoes)
                except TypeError:
                    # se não aceitar, já tentamos antes no caminho do arquivo
                    pass

            writer.add_page(page)

        ordered_path = os.path.join(upload_folder, f"ordered_{uuid.uuid4().hex}.pdf")
        with open(ordered_path, "wb") as out_f:
            writer.write(out_f)
        temp_source = ordered_path

        try:
            os.remove(input_path)
        except OSError:
            pass

    # 4) Flatten com QPDF (evita “linhas sumindo” após GS)
    flat_path = os.path.join(upload_folder, f"flat_{uuid.uuid4().hex}.pdf")
    try:
        _qpdf_flatten(temp_source, flat_path)
        stage_source = flat_path
    except Exception:
        stage_source = temp_source  # segue mesmo sem flatten

    original_pages = _page_count(stage_source)
    original_size  = os.path.getsize(stage_source)

    # 5) Perfil interno
    internal_profile = resolve_profile(profile)

    # 6) Lossless direto (sem GS)
    if internal_profile == "lossless":
        out_lossless = os.path.join(upload_folder, f"comprimido_{basename}_{uuid.uuid4().hex}.pdf")
        try:
            _qpdf_optimize_lossless(stage_source, out_lossless)
            return out_lossless
        finally:
            for p in (flat_path,):
                try: os.remove(p)
                except OSError: pass

    # 7) Ghostscript com checagens de integridade/ganho
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
        try:
            _qpdf_optimize_lossless(stage_source, safe_out)
            return safe_out
        except Exception:
            return stage_source
    finally:
        # Limpeza de intermediários (não remove o arquivo final!)
        for p in (flat_path,):
            try: os.remove(p)
            except OSError: pass