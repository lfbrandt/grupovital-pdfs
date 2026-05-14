# -*- coding: utf-8 -*-
"""
converter_service.py — Conversões e utilidades PDF/planilhas
Principais pontos:
- Imagem → PDF sai em A4 (ou Letter), centralizada, com margens mínimas. Auto-paisagem opcional.
- Respeita EXIF Orientation (ImageOps.exif_transpose) antes de qualquer cálculo de layout.
- Merge: pode normalizar para A4/Letter com PDFFitPage e AutoRotate configurável (none/page/all).
- PDF → XLSX no estilo “modelo” (já existente), com OCR opcional e vários fallbacks.
"""
from __future__ import annotations

import os, re, tempfile, subprocess, shutil, logging, time, platform
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Dict, Any, Tuple

from PIL import Image, ImageOps
from werkzeug.exceptions import BadRequest

from ..utils.limits import enforce_pdf_page_limit
# 🔒 sandbox (mesmo mecanismo usado no merge_service)
from .sandbox import run_in_sandbox
from flask import has_request_context, request  # (para tentar bytes_in via Content-Length)
from ..utils.stats import record_job_event      # (7.1) métricas

logger = logging.getLogger(__name__)

IMG_EXTS   = {'jpg','jpeg','png','bmp','tif','tiff','webp'}
DOC_EXTS   = {'doc','docx','odt','rtf','txt','html','htm','ppt','pptx','odp'}
SHEET_EXTS = {'csv','xls','xlsx','ods'}

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

# =========================
# Merge normalization envs
# =========================
MERGE_NORMALIZE_MODE = (os.environ.get("MERGE_NORMALIZE_MODE", "auto") or "auto").lower()  # auto|always|off
MERGE_NORMALIZE_AUTOROTATE = (os.environ.get("MERGE_NORMALIZE_AUTOROTATE", "none") or "none").lower()  # none|page|all
MERGE_STRIP_ROTATE = (os.environ.get("MERGE_STRIP_ROTATE", "0") == "1")

# Tamanhos padrão em pontos (1/72")
SIZES_PT = {
    "A4":     (595.2756, 841.8898),  # 210 x 297 mm
    "LETTER": (612.0,   792.0),      # 8.5 x 11 in
}

# ---------------- TMP helpers ----------------
def _save_upload_to_tmp(upload_file, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix); os.close(fd)
    upload_file.stream.seek(0)
    with open(path, 'wb') as out:
        shutil.copyfileobj(upload_file.stream, out)
    return path

def _tmp_out_path(ext: str) -> str:
    fd, path = tempfile.mkstemp(suffix='.' + ext.lstrip('.')); os.close(fd)
    return path

def _unique_out_path(out_dir: str, base: str, ext: str) -> str:
    out_dir = os.path.abspath(out_dir); os.makedirs(out_dir, exist_ok=True)
    candidate = os.path.join(out_dir, f"{base}.{ext}"); i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(out_dir, f"{base} ({i}).{ext}"); i += 1
    return candidate

# ======================================================================
# Imagem → PDF A4/Letter (ReportLab; fallback via PIL mantendo página real)
# Env:
#   IMG2PDF_MODE=fit|cover (default fit)   → fit mantém tudo visível; cover “preenche” (pode cortar)
#   IMG2PDF_MARGIN_PT=18                   → margem em pontos
#   IMG2PDF_LANDSCAPE_AUTO=1|0 (default 1) → paisagem automática se imagem deitada
#   IMG2PDF_DPI=300                        → usado no fallback PIL
#   IMG2PDF_PAGE_SIZE=A4|LETTER            → força tamanho; default A4
# ======================================================================
def _apply_exif(img: Image.Image) -> Image.Image:
    """Aplica rotação EXIF (se houver) antes de qualquer conversão/resize."""
    try:
        return ImageOps.exif_transpose(img)
    except Exception:
        return img

def _image_to_pdf(in_path: str, out_path: str) -> None:
    mode = (os.environ.get("IMG2PDF_MODE", "fit") or "fit").lower()
    margin = float(os.environ.get("IMG2PDF_MARGIN_PT", "18"))
    auto_land = os.environ.get("IMG2PDF_LANDSCAPE_AUTO", "1") == "1"
    page_size_name = (os.environ.get("IMG2PDF_PAGE_SIZE", "A4") or "A4").upper()
    base_w_pt, base_h_pt = SIZES_PT.get(page_size_name, SIZES_PT["A4"])

    # Tenta ReportLab (precisão A4/Letter garantida)
    try:
        from reportlab.pdfgen import canvas as _rl_canvas
        from reportlab.lib.pagesizes import A4 as _A4, LETTER as _LETTER, landscape as _landscape
        from reportlab.lib.utils import ImageReader

        size_map = {"A4": _A4, "LETTER": _LETTER}
        base_size = size_map.get(page_size_name, _A4)

        im = Image.open(in_path)
        frames: List[Image.Image] = []
        try:
            # coleta frames (TIFF multipáginas etc.)
            i = 0
            while True:
                im.seek(i)
                fr = _apply_exif(im.copy())
                frames.append(fr.convert("RGB"))
                i += 1
        except EOFError:
            pass
        if not frames:
            frames = [_apply_exif(im.copy()).convert("RGB")]

        c: Optional[_rl_canvas.Canvas] = None
        for f in frames:
            iw, ih = f.size
            pagesize = base_size
            if auto_land and iw > ih * 1.05:
                pagesize = _landscape(base_size)
            PW, PH = pagesize

            max_w, max_h = PW - 2*margin, PH - 2*margin
            scale = max(max_w / iw, max_h / ih) if mode == "cover" else min(max_w / iw, max_h / ih)
            tw, th = iw * scale, ih * scale
            x, y = (PW - tw) / 2.0, (PH - th) / 2.0

            # trata transparência para fundo branco
            if f.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", f.size, (255, 255, 255))
                if f.mode in ("RGBA", "LA"):
                    bg.paste(f, mask=f.split()[-1])
                else:
                    bg.paste(f)
                f = bg

            if c is None:
                c = _rl_canvas.Canvas(out_path, pagesize=pagesize)
            else:
                c.setPageSize(pagesize)

            c.drawImage(ImageReader(f), x, y, width=tw, height=th,
                        preserveAspectRatio=True, mask='auto')
            c.showPage()
        if c:
            c.save()
        try:
            im.close()
        except Exception:
            pass
        return
    except Exception as e:
        logger.debug("ReportLab indisponível (%s). Usando fallback PIL para A4/Letter.", e)

    # Fallback PIL: cria uma “lona” A4/Letter em pixels (DPI alto) e salva como PDF
    dpi = int(os.environ.get("IMG2PDF_DPI", "300"))
    base_w_px = int(round(base_w_pt / 72.0 * dpi))
    base_h_px = int(round(base_h_pt / 72.0 * dpi))
    margin_px = int(round(margin / 72.0 * dpi))

    im = Image.open(in_path)
    pages: List[Image.Image] = []
    try:
        frames: List[Image.Image] = []
        try:
            i = 0
            while True:
                im.seek(i)
                frm = _apply_exif(im.copy()).convert("RGB")
                frames.append(frm)
                i += 1
        except EOFError:
            pass
        if not frames:
            frames = [_apply_exif(im.copy()).convert("RGB")]

        for f in frames:
            # auto paisagem
            W, H = (base_w_px, base_h_px)
            if auto_land and f.width > f.height * 1.05:
                W, H = (base_h_px, base_w_px)

            canvas_img = Image.new("RGB", (W, H), (255, 255, 255))
            max_w, max_h = W - 2*margin_px, H - 2*margin_px

            scale = max(max_w / f.width, max_h / f.height) if mode == "cover" else min(max_w / f.width, max_h / f.height)
            tw, th = max(1, int(round(f.width * scale))), max(1, int(round(f.height * scale)))
            f2 = f.resize((tw, th), Image.LANCZOS)
            x, y = (W - tw) // 2, (H - th) // 2
            canvas_img.paste(f2, (x, y))
            pages.append(canvas_img)

        pages[0].save(out_path, save_all=True, append_images=pages[1:],
                      format='PDF', resolution=float(dpi))
    finally:
        try: im.close()
        except Exception: pass

# ---------------- LibreOffice helpers ----------------
# Caminhos padrão do LibreOffice no Windows (adicione mais se necessário)
_LO_WIN_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    r"C:\Program Files\LibreOffice 7\program\soffice.exe",
    r"C:\Program Files\LibreOffice 6\program\soffice.exe",
]

def _soffice_bin() -> str:
    """
    Resolve o binário do soffice/LibreOffice.
    Ordem de preferência:
      1. SOFFICE_BIN (env)
      2. LIBREOFFICE_BIN (env)
      3. shutil.which("soffice") / shutil.which("soffice.com") / shutil.which("libreoffice")
      4. Caminhos fixos comuns no Windows
    Levanta RuntimeError com mensagem clara se não encontrar.
    """
    import shutil as _sh

    # 1. Variáveis de ambiente explícitas
    from_env = os.environ.get('SOFFICE_BIN') or os.environ.get('LIBREOFFICE_BIN')
    if from_env:
        if os.path.isfile(from_env):
            logger.info("[LO] soffice via env: %s", from_env)
            return from_env
        logger.warning("[LO] SOFFICE_BIN/LIBREOFFICE_BIN configurado mas não encontrado: %s", from_env)

    # 2. PATH do sistema
    for candidate in ("soffice", "soffice.com", "libreoffice"):
        found = _sh.which(candidate)
        if found:
            logger.info("[LO] soffice via PATH (%s): %s", candidate, found)
            return found

    # 3. Caminhos fixos Windows
    if os.name == 'nt':
        for path in _LO_WIN_PATHS:
            if os.path.isfile(path):
                logger.info("[LO] soffice via caminho fixo Windows: %s", path)
                return path

    raise RuntimeError(
        "LibreOffice não encontrado. Instale o LibreOffice e configure SOFFICE_BIN "
        "no arquivo .env apontando para soffice.exe. "
        "Exemplo: SOFFICE_BIN=C:\\Program Files\\LibreOffice\\program\\soffice.exe"
    )

def _lo_convert(in_path: str, out_dir: str, out_ext: str,
                filter_name: Optional[str] = None, filter_opts: Optional[str] = None) -> str:
    convert_to = out_ext
    if filter_name and filter_opts:
        convert_to = f"{out_ext}:{filter_name}:{filter_opts}"
    elif filter_name:
        convert_to = f"{out_ext}:{filter_name}"

    lo_timeout = int(os.environ.get("LO_CONVERT_TIMEOUT_SEC", "120"))

    try:
        soffice = _soffice_bin()
    except RuntimeError:
        raise  # já tem mensagem clara; sobe como RuntimeError (capturado em converter.py → 500 com log)

    cmd = [
        soffice,
        '--headless', '--safe-mode', '--nologo', '--nodefault', '--nolockcheck', '--invisible',
        '--convert-to', convert_to, '--outdir', out_dir, in_path
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False, timeout=lo_timeout)
    except FileNotFoundError:
        raise RuntimeError(
            "LibreOffice não encontrado. Instale o LibreOffice e configure SOFFICE_BIN no .env."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"LibreOffice excedeu o tempo limite de {lo_timeout}s ao converter para {out_ext}.")

    if proc.returncode != 0:
        err = (proc.stderr or b'').decode(errors='ignore')[:800]
        raise RuntimeError(f"LibreOffice falhou (rc={proc.returncode}) ao converter para {out_ext}. Detalhes: {err}")

    base = os.path.splitext(os.path.basename(in_path))[0]
    produced = os.path.join(out_dir, f"{base}.{out_ext}")
    if not os.path.exists(produced):
        for fn in os.listdir(out_dir):
            if fn.lower().startswith(base.lower()+".") and fn.lower().endswith("."+out_ext):
                produced = os.path.join(out_dir, fn); break
    if not os.path.exists(produced):
        raise RuntimeError(f"Conversão não gerou saída {out_ext}")
    return produced

# ---------------- Camelot / Ghostscript env ----------------
def _prepare_camelot_env() -> None:
    import shutil as _sh
    gs = (os.environ.get("GS_BIN") or os.environ.get("GHOSTSCRIPT_BIN")
          or _sh.which("gswin64c") or _sh.which("gs"))
    if not gs:
        raise RuntimeError("Ghostscript não encontrado. Instale GS 64-bit e/ou defina GS_BIN no .env")
    os.environ["PATH"] = os.path.dirname(gs) + os.pathsep + os.environ.get("PATH", "")
    os.environ["GHOSTSCRIPT_PATH"] = gs
    os.environ["GS_PROG"] = gs
    try:
        import cv2  # noqa: F401
    except Exception as e:
        raise RuntimeError("OpenCV não encontrado (pip install opencv-python-headless).") from e

def _bin_exists(bin_name: str) -> bool:
    from shutil import which
    return which(bin_name) is not None

# ---------------- OCR helper ----------------
def _pdf_has_selectable_text(in_pdf: str) -> bool:
    try:
        import pdfplumber
        with pdfplumber.open(in_pdf) as pdf:
            for page in pdf.pages:
                if (page.chars or []) or (page.extract_text() or "").strip():
                    return True
    except Exception:
        pass
    return False

def _try_ocr(in_pdf: str) -> str:
    if os.environ.get("OCR_ON_PDF_TO_XLSX","0") != "1":
        return in_pdf
    if not _bin_exists("ocrmypdf"):
        return in_pdf
    enforce_pdf_page_limit(in_pdf, label="PDF para OCR")
    out_pdf = _tmp_out_path("pdf")
    ocr_lang = os.environ.get("OCR_LANGS","por+eng")
    ocr_timeout = int(os.environ.get("OCR_TIMEOUT_SEC","300"))
    cmd = ["ocrmypdf","--skip-text","--force-ocr","--rotate-pages",
           "--tesseract-timeout","60","-l",ocr_lang,in_pdf,out_pdf]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       check=True, timeout=ocr_timeout)
        if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 0:
            return out_pdf
    except Exception:
        try: os.remove(out_pdf)
        except Exception: pass
    return in_pdf

# ---------------- Excel helpers (mantidos) ----------------
EXCEL_DANGEROUS_PREFIXES = ("=","+","-","@")
def _excel_safe_str(s: Any) -> str:
    s = "" if s is None else str(s).replace("\r"," ").replace("\n"," ").strip()
    return "'" + s if s.startswith(EXCEL_DANGEROUS_PREFIXES) else s

_num_re = re.compile(r"^[\sR\$\-+()]?[\d\.\,\s]+%?$")
def _maybe_number(s: Any):
    if s is None: return None
    txt = str(s).strip()
    if not txt or not _num_re.match(txt.replace("R$"," ").strip()): return None
    t = txt.replace("R$"," ").replace(" ","").strip()
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg = True; t = t[1:-1]
    is_percent = t.endswith("%")
    if is_percent: t = t[:-1]
    if "," in t and "." in t:
        t = t.replace(".","").replace(",",".")
    elif "," in t:
        t = t.replace(".","").replace(",",".")
    else:
        t = t.replace(",", "")
    try:
        val = Decimal(t)
        val = -val if neg else val
        if is_percent: val = val / Decimal(100)
        return val
    except InvalidOperation:
        return None

def _make_unique_columns(cols: List[str]) -> List[str]:
    seen: Dict[str,int] = {}
    out: List[str] = []
    for c in cols:
        base = (c or "Coluna").strip() or "Coluna"
        if base not in seen:
            seen[base] = 1; out.append(base)
        else:
            seen[base] += 1; out.append(f"{base} ({seen[base]})")
    return out

def _trim_headers(headers: List[str], max_len: int = 80) -> List[str]:
    out: List[str] = []
    used: set[str] = set()
    for h in headers:
        s = str(h or "").strip()
        if len(s) > max_len:
            s = s[:max_len-1] + "…"
        base = s or "Coluna"
        cand, i = base, 2
        while cand in used:
            cand = f"{base} ({i})"; i += 1
        used.add(cand); out.append(cand)
    return out

def _clean_and_infer(df):
    import pandas as pd
    # Guard: df inválido ou vazio
    if df is None:
        return pd.DataFrame(), {}
    try:
        df = df.copy().map(lambda x: "" if x is None else str(x).replace("\r"," ").replace("\n"," ").strip())
    except Exception:
        return pd.DataFrame(), {}
    df = df.loc[:, (df != "").any(axis=0)]
    df = df[(df != "").any(axis=1)]
    if df.empty or len(df) == 0:
        return df, {}

    # Rastreia POSIÇÃO inteira (pos) e label (i) separadamente.
    # df.iloc usa posição; df.loc usa label — misturar os dois causa IndexError.
    header_pos, best_fill = 0, -1.0
    for pos, (i, row) in enumerate(df.iterrows()):
        non_empty = (row != "").sum()
        fill = non_empty / max(1, len(row))
        if fill > best_fill and non_empty >= 2:
            best_fill, header_pos = fill, pos
        if fill >= 0.7:
            header_pos = pos
            break

    # Valida que header_pos está dentro dos limites
    if header_pos >= len(df):
        return pd.DataFrame(), {}

    header = [h if h else f"Coluna {j+1}" for j, h in enumerate(list(df.iloc[header_pos].values))]
    header = _trim_headers(_make_unique_columns([str(h) for h in header]), max_len=80)
    df = df.iloc[header_pos + 1:].reset_index(drop=True)
    df.columns = header

    df = df[~(df.apply(lambda r: (list(r.values) == header), axis=1))]

    meta: Dict[str, Dict[str, Any]] = {}
    for col in list(df.columns):
        series = df[col].astype(str)
        parsed = [_maybe_number(v) for v in series]
        ratio = sum(p is not None for p in parsed) / max(1, len(parsed))
        if ratio >= 0.6:
            has_percent  = any(str(v).strip().endswith("%") for v in series)
            has_currency = any("R$" in str(v) for v in series)
            df[col] = [(p if p is not None else None) for p in parsed]
            meta[col] = {"type": "percent" if has_percent else ("money" if has_currency else "number")}
        else:
            df[col] = [_excel_safe_str(v) for v in series]
            meta[col] = {"type": "text"}

    df = df.loc[:, df.notna().any(axis=0)]
    if len(set(df.columns)) != len(df.columns):
        df.columns = _make_unique_columns(list(df.columns))
    return df, meta

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return (s.replace("ç","c").replace("á","a").replace("à","a").replace("ã","a").replace("â","a")
              .replace("é","e").replace("ê","e").replace("í","i").replace("ó","o").replace("ô","o")
              .replace("õ","o").replace("ú","u").replace("%"," pct ").replace("º",""))

# ---------------- Alvos/schema, detecção de áreas, etc. (mantidos) ----------------
def _load_target_schema_from_env() -> Optional[List[str]]:
    import pandas as pd
    schema_file = os.environ.get("XLSM_SCHEMA_FILE")
    if not schema_file:
        default_schema = os.path.join(os.getcwd(), "envs", "modelo.xlsx")
        if os.path.exists(default_schema):
            schema_file = default_schema
    if schema_file and ((schema_file.startswith('"') and schema_file.endswith('"')) or
                        (schema_file.startswith("'") and schema_file.endswith("'"))):
        schema_file = schema_file[1:-1]
    if schema_file and os.path.exists(schema_file):
        try:
            xls = pd.ExcelFile(schema_file)
            df = pd.read_excel(schema_file, sheet_name=xls.sheet_names[0], header=None)
            for _, row in df.iterrows():
                vals = [str(v).strip() for v in row.values if str(v).strip()]
                if len(vals) >= 2:
                    return vals
        except Exception as e:
            logger.debug("Falha ao ler XLSM_SCHEMA_FILE: %s", e)
    cols_env = os.environ.get("XLSM_TARGET_COLUMNS")
    if cols_env:
        cols = [c.strip() for c in cols_env.split(",") if c.strip()]
        if len(cols) >= 2:
            return cols
    return None

def _bbox_plumber_to_camelot(page_height: float, bbox_plumber: Tuple[float,float,float,float]) -> str:
    x0, top, x1, bottom = bbox_plumber
    y_top_c = page_height - top
    y_bot_c = page_height - bottom
    return f"{x0},{y_top_c},{x1},{y_bot_c}"

def _cluster_positions(vals, tol: float = 2.5):
    vals = sorted(float(v) for v in vals)
    if not vals: return []
    clusters, cur = [], [vals[0]]
    for v in vals[1:]:
        if abs(v - cur[-1]) <= tol:
            cur.append(v)
        else:
            clusters.append(sum(cur)/len(cur)); cur = [v]
    clusters.append(sum(cur)/len(cur))
    return clusters

def _detect_table_bbox_and_columns(page, header_hints=None) -> Tuple[Tuple[float,float,float,float], List[float]]:
    header_hints = header_hints or ["Nome","Segurado","Valor","Part."]
    words = page.extract_words() or []
    W, H = page.width, page.height
    header_y_top, left_edge, right_edge = None, W*0.07, W*0.98
    for w in words:
        txt = (w.get("text") or "").strip()
        if any(h.lower() in txt.lower() for h in header_hints):
            if header_y_top is None or w["top"] < header_y_top:
                header_y_top = w["top"]
            left_edge = min(left_edge, w["x0"])
            right_edge = max(right_edge, w["x1"])
    if header_y_top is None:
        header_y_top = H*0.20
    bottom_edge = H*0.90
    try:
        hlines = [l for l in (page.lines or []) if abs(l["y0"]-l["y1"]) < 0.5 and (l["x1"]-l["x0"]) > (W*0.6)]
        if hlines:
            bottom_edge = max(l["y0"] for l in hlines if l["y0"] > header_y_top+5)
    except Exception:
        pass
    top, bottom = max(0, header_y_top-6), min(H-1, bottom_edge+6)
    x0, x1 = max(0, left_edge-6), min(W-1, right_edge+6)

    vxs = []
    try:
        for l in (page.lines or []):
            if abs(l["x0"]-l["x1"]) >= 0.5:  # queremos linhas verticais
                continue
            y_top, y_bot = min(l["y0"],l["y1"]), max(l["y0"],l["y1"])
            if y_bot>=top and y_top<=bottom and x0<=l["x0"]<=x1:
                vxs.append(l["x0"])
    except Exception:
        pass
    cols = [c for c in _cluster_positions(vxs, tol=2.0) if (c-x0)>5 and (x1-c)>5]
    return (x0, top, x1, bottom), cols

def _extract_tables_smart(src_pdf: str) -> List['pd.DataFrame']:
    import pandas as pd, pdfplumber, camelot
    dpi = int(os.environ.get("PDF_TO_XLSX_DPI","200"))
    line_scale = int(os.environ.get("PDF_TO_XLSX_LINE_SCALE","80"))
    process_bg = os.environ.get("PDF_PROCESS_BACKGROUND","0") == "1"
    allow_stream = os.environ.get("PDF_TO_XLSX_ALLOW_STREAM","0") == "1"
    dfs: List[pd.DataFrame] = []

    pages_env = os.environ.get("PDF_PAGE_RANGE")
    def page_allowed(i: int) -> bool:
        if not pages_env: return True
        def parse_range(spec: str):
            for token in spec.split(','):
                token = token.strip()
                if '-' in token:
                    a,b = token.split('-',1)
                    try: a=int(a); b=int(b)
                    except: continue
                    for v in range(a,b+1): yield v
                else:
                    try: yield int(token)
                    except: pass
        return i in set(parse_range(pages_env))

    header_hints = os.environ.get(
        "PDF_HEADER_HINTS",
        "Cia,Suc,Apol.,Cob,Fatura,Estipulante,CPF,Serviço,Quantidade,Valor,Conta,Ramo,Data Emissão"
    ).split(",")

    with pdfplumber.open(src_pdf) as pdf:
        for idx, p in enumerate(pdf.pages, start=1):
            if not page_allowed(idx): continue

            try:
                preview = (p.extract_text() or "").strip().upper()[:200]
                if "MENSAGENS" in preview:
                    continue
            except Exception:
                pass

            bbox_pl, cols = _detect_table_bbox_and_columns(p, header_hints=header_hints)
            area = _bbox_plumber_to_camelot(p.height, bbox_pl)

            found = False
            try:
                tbs = camelot.read_pdf(
                    src_pdf, flavor="lattice", pages=str(idx),
                    table_areas=[area], line_scale=line_scale, strip_text="\n",
                    process_background=process_bg, copy_text=["h","v"], shift_text=["l","t"],
                    dpi=dpi
                )
                for t in getattr(tbs, "tables", tbs):
                    if getattr(t, "df", None) is None or getattr(t.df, "empty", True):
                        continue
                    dfs.append(t.df); found = True
            except Exception as e:
                logger.debug("SMART lattice falhou pág %s: %s", idx, e)

            if (not found) and allow_stream:
                try:
                    col_str = ",".join(str(int(x)) for x in cols) if cols else None
                    tbs = camelot.read_pdf(
                        src_pdf, flavor="stream", pages=str(idx),
                        table_areas=[area], columns=[col_str] if col_str else None,
                        strip_text="\n", dpi=dpi
                    )
                    for t in getattr(tbs, "tables", tbs):
                        if getattr(t, "df", None) is None or getattr(t.df, "empty", True):
                            continue
                        dfs.append(t.df)
                except Exception as e:
                    logger.debug("SMART stream falhou pág %s: %s", idx, e)
    return dfs

def _find_dense_table_areas(pdf_path: str) -> Dict[int, List[str]]:
    import pdfplumber
    areas_by_page: Dict[int, List[str]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages, start=1):
            W, H = p.width, p.height
            lines, rects = p.lines or [], p.rects or []
            candidates = []
            for r in rects:
                w = abs(r["x1"] - r["x0"]); h = abs(r["y1"] - r["y0"])
                if w*h < (W*H*0.05):
                    continue
                inside = 0
                for ln in lines:
                    x0, x1 = min(ln["x0"],ln["x1"]), max(ln["x0"],ln["x1"])
                    y0, y1 = min(ln["y0"],ln["y1"]), max(ln["y0"],ln["y1"])
                    if (x0 >= r["x0"]-2 and x1 <= r["x1"]+2 and y0 >= r["y0"]-2 and y1 <= r["y1"]+2):
                        inside += 1
                score = inside / max(1.0, (w*h)/(W*H))
                candidates.append((score, r))
            candidates.sort(key=lambda t: t[0], reverse=True)
            picks = []
            for _, r in candidates[:2]:
                picks.append(_bbox_plumber_to_camelot(H, (r["x0"],r["y0"],r["x1"],r["y1"])))
            if picks:
                areas_by_page[i] = picks
    return areas_by_page

# ---------------- Escrita XLSX ----------------
def _write_minimal_xlsx(out_path: str, message: str = "Nenhuma tabela detectada. Tente habilitar OCR.") -> None:
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Dados"
    ws["A1"] = "Aviso"; ws["A2"] = message
    ws.column_dimensions["A"].width = min(80, max(20, int(len(message) * 0.9)))
    wb.save(out_path)

def _format_openpyxl_sheet(ws, col_meta: Dict[str, Dict[str, Any]]):
    from openpyxl.styles import Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    max_col, max_row = ws.max_column, ws.max_row
    if max_col and max_row:
        ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"

    for j, col_name_cell in enumerate(ws[1], start=1):
        col_name = str(col_name_cell.value or "")
        meta = col_meta.get(col_name, {"type":"text"})
        col_letter = get_column_letter(j)
        if meta["type"] == "percent":
            for r in range(2, max_row+1):
                c = ws[f"{col_letter}{r}"]; c.number_format = "0.00%"; c.alignment = Alignment(horizontal="right")
        elif meta["type"] == "money":
            for r in range(2, max_row+1):
                c = ws[f"{col_letter}{r}"]; c.number_format = "#,##0.00"; c.alignment = Alignment(horizontal="right")
        elif meta["type"] == "number":
            for r in range(2, max_row+1):
                c = ws[f"{col_letter}{r}"]; c.number_format = "#,##0.########"; c.alignment = Alignment(horizontal="right")
        else:
            if "data" in _norm(col_name):
                for r in range(2, max_row+1):
                    c = ws[f"{col_letter}{r}"]; c.number_format = "dd/mm/yyyy"; c.alignment = Alignment(horizontal="center")
            else:
                for r in range(2, max_row+1):
                    ws[f"{col_letter}{r}"].alignment = Alignment(horizontal="left")

    for j in range(1, max_col+1):
        col_letter = get_column_letter(j); max_len = 0
        for r in range(1, max_row+1):
            v = ws[f"{col_letter}{r}"].value
            if v is None: continue
            s = str(v) + ("   " if r == 1 else "")
            max_len = max(max_len, len(s))
        ws.column_dimensions[col_letter].width = max(10, min(60, int(max_len*1.15)))

    add_table = os.environ.get("XLSX_ADD_TABLE","0") == "1"
    if add_table and max_row >= 2 and max_col >= 1:
        import uuid as _uuid
        tbl_name = f"Tbl_{_uuid.uuid4().hex[:8]}"
        ref = f"A1:{get_column_letter(max_col)}{max_row}"
        table = Table(displayName=tbl_name, ref=ref)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2",
                                             showRowStripes=True, showColumnStripes=False)
        ws.add_table(table)

    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col):
        for cell in row:
            cell.border = border

def _drop_all_empty_rows(df):
    import pandas as pd
    df2 = df.copy()
    for c in df2.columns:
        df2[c] = df2[c].replace("", pd.NA)
    df2 = df2.dropna(how="all")
    return df2

def _map_columns_to_schema_with_stats(df, target_cols: List[str]):
    import pandas as pd
    from difflib import SequenceMatcher
    src_cols = list(df.columns)
    used = set(); matched = 0

    def best_match(target: str) -> Optional[str]:
        tnorm = _norm(target)
        best, best_score = None, 0.0
        for c in src_cols:
            if c in used: continue
            score = SequenceMatcher(None, _norm(c), tnorm).ratio()
            if _norm(c) in tnorm or tnorm in _norm(c):
                score += 0.15
            if score > best_score:
                best, best_score = c, score
        return best if best_score >= 0.55 else None

    out = {}
    for tgt in target_cols:
        match = best_match(tgt)
        out[tgt] = df[match] if match else pd.Series([""] * len(df))
        if match:
            used.add(match); matched += 1
    return pd.DataFrame(out), matched, len(target_cols)

def _rescue_with_stream(src_pdf: str, pages: str) -> List['pd.DataFrame']:
    import camelot, pandas as pd
    dpi = int(os.environ.get("PDF_TO_XLSX_DPI","200"))
    tbs = camelot.read_pdf(src_pdf, flavor="stream", pages=pages or "all",
                           strip_text="\n", dpi=dpi)
    return [t.df for t in getattr(tbs, "tables", tbs)
            if getattr(t, "df", None) is not None and not getattr(t.df, "empty", True)]

def _pdfplumber_tables_dfs(src_pdf: str, pages: str) -> List['pd.DataFrame']:
    import pdfplumber, pandas as pd
    dfs = []
    pages_set = None
    if pages and pages != "all":
        def parse_range(spec: str):
            for token in spec.split(','):
                token = token.strip()
                if '-' in token:
                    a,b = token.split('-',1)
                    try: a=int(a); b=int(b)
                    except: continue
                    for v in range(a,b+1): yield v
                else:
                    try: yield int(token)
                    except: pass
        pages_set = set(parse_range(pages))
    with pdfplumber.open(src_pdf) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            if pages_set and i not in pages_set: continue
            for table in (page.extract_tables() or []):
                import pandas as pd
                dfs.append(pd.DataFrame(table))
    return dfs

# ---------------- Conversores PDF ----------------
def _pdf_to_docx(in_pdf: str, out_dir: str) -> str:
    from pdf2docx import Converter
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(in_pdf))[0]
    out_path = _unique_out_path(out_dir, base, "docx")
    cv = Converter(in_pdf)
    try:
        cv.convert(out_path, start=0, end=None)
    finally:
        cv.close()
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("pdf2docx falhou ao gerar DOCX")
    return out_path

# ============================================================
# Fase 1 — Extração inteligente: tabelas de coparticipação
# ============================================================

# Código no padrão  1.01.01012  (dentro de qualquer string)
_RE_COD_ANYWHERE = re.compile(r'\d\.\d{2}\.\d{5}')
# Código no início exato de um token
_RE_COD_START    = re.compile(r'^\d\.\d{2}\.\d{5}')
# Separador para split em múltiplos blocos numa célula gigante
_RE_COD_SPLIT    = re.compile(r'(?=\d\.\d{2}\.\d{5})')

# Valores monetários: aceita "R$ 135,00", "135,00", "135.00"
_RE_MONEY_BR  = re.compile(r'R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}')
_RE_MONEY_NUM = re.compile(r'\b\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\b')

# Títulos de seção
_RE_SECAO = re.compile(
    r'EXEMPLOS\s+DE\s+(?:EXAMES|CO(?:PARTICIPAÇÃO|PARTICIPA[CÇ][AÃ]O))'
    r'|OUTROS\s+EXEMPLOS',
    re.IGNORECASE,
)

# Textos de observação/rodapé — devem ser descartados
_RE_OBS = re.compile(
    r'\*\s*Obs[:\s]|Com\s+exce[çc][aã]o\s+dos\s+valores'
    r'|referem-se\s+ao\s+custo|pode\s+haver\s+varia[çc][aã]o'
    r'|ANEXO\s+[IVX]+',
    re.IGNORECASE,
)

_HEADER_TOKENS = frozenset({
    'codigo', 'código', 'procedimento', 'valor unimed',
    'co-part', 'flex', 'pleno', 'cop.', 'co-participação',
    'coparticipação', '20%', '30%', '40%', '50%',
})


def _format_brl(raw: str) -> str:
    """
    Normaliza um valor monetário bruto para "R$ NNN,NN".
    Aceita: "R$ 135,00", "135,00", "135.00", "1.350,00", "1350.00".
    Retorna a string original se não conseguir converter.
    """
    s = str(raw).strip()
    # já está no formato desejado
    if _RE_MONEY_BR.match(s):
        return s
    # remove prefixo R$
    s_clean = re.sub(r'^R\$\s*', '', s).strip()
    # normaliza separadores: "1.350,00" → "1350.00", "135,00" → "135.00"
    if ',' in s_clean and '.' in s_clean:
        # formato BR com milhar: 1.350,00
        s_clean = s_clean.replace('.', '').replace(',', '.')
    elif ',' in s_clean:
        # sem milhar: 135,00
        s_clean = s_clean.replace(',', '.')
    try:
        val = float(s_clean)
        # formata de volta para BR
        # separa inteiro e decimal
        inteiro = int(val)
        cents   = round((val - inteiro) * 100)
        # milhar no estilo BR
        inteiro_fmt = f'{inteiro:,}'.replace(',', '.')
        return f'R$ {inteiro_fmt},{cents:02d}'
    except (ValueError, TypeError):
        return raw  # devolve original se falhar


def _extract_money_values(text: str) -> List[str]:
    """
    Extrai todos os valores monetários de um texto, normalizados para R$ NNN,NN.
    Tenta primeiro o padrão R$; depois padrão numérico genérico.
    """
    found = _RE_MONEY_BR.findall(text)
    if found:
        return [_format_brl(v) for v in found]
    # fallback: padrão numérico (ex.: "135.00" ou "135,00")
    found_num = _RE_MONEY_NUM.findall(text)
    return [_format_brl(v) for v in found_num]


def _is_obs_line(text: str) -> bool:
    """Retorna True se a linha é rodapé/observação e deve ser descartada."""
    return bool(_RE_OBS.search(text))


def _is_header_line(text: str) -> bool:
    """Retorna True se a linha é cabeçalho de tabela a ser descartado."""
    low = text.lower()
    hits = sum(1 for tok in _HEADER_TOKENS if tok in low)
    # cabeçalho puro: ≥3 tokens E sem código de procedimento
    return hits >= 3 and not _RE_COD_ANYWHERE.search(text)


def _is_secao_line(text: str) -> bool:
    return bool(_RE_SECAO.search(text))


def _is_total_line(text: str) -> bool:
    return 'TOTAL DE COPARTICI' in text.upper()


def _row_to_text(row) -> str:
    """Une as células não-vazias de uma linha do DataFrame."""
    return ' '.join(str(v).strip() for v in row if str(v).strip())


def _df_to_lines(df) -> List[str]:
    """
    Converte cada linha do DataFrame em uma string de texto limpa,
    descartando rodapés e células vazias.
    Linhas que contenham vários códigos são divididas em sublistas.
    """
    lines: List[str] = []
    for _, row in df.iterrows():
        raw = _row_to_text(row)
        if not raw:
            continue
        if _is_obs_line(raw):
            continue
        # célula gigante com múltiplos códigos concatenados?
        parts = _RE_COD_SPLIT.split(raw)
        parts = [p.strip() for p in parts if p.strip()]
        lines.extend(parts)
    return lines


def _looks_like_coparticipacao_table(df) -> bool:
    """Retorna True se o DataFrame parece ser tabela de coparticipação."""
    if df is None or df.empty:
        return False
    text = ' '.join(
        str(v) for row in df.values for v in row if v is not None
    ).upper()
    signals = 0
    if 'CO-PART' in text:
        signals += 2
    if 'VALOR UNIMED' in text:
        signals += 2
    if 'TOTAL DE COPARTICI' in text:
        signals += 2
    if _RE_SECAO.search(text):
        signals += 2
    if _RE_COD_ANYWHERE.search(text):
        signals += 2
    return signals >= 4


def _normalize_coparticipacao_table(df) -> 'pd.DataFrame':
    """
    Normaliza um DataFrame bruto de tabela de coparticipação para o esquema
    fixo de 9 colunas. Estratégia:
      1. Converter cada linha em texto plano (via _df_to_lines).
      2. Classificar cada linha em: seção / cabeçalho / obs / total /
         procedimento / continuação.
      3. Montar records limpos, concatenando continuações ao procedimento
         anterior.
      4. Normalizar valores para "R$ NNN,NN".
      5. Limitar a 5 valores por linha de procedimento (Valor Unimed +
         4 faixas de copart.) para evitar coluna extra duplicada.
    """
    import pandas as pd

    OUT_COLS = [
        'Secao', 'Codigo', 'Procedimento',
        'Valor Unimed', 'Copart 20%', 'Copart 30%', 'Copart 40%', 'Copart 50%',
        'Tipo Linha',
    ]

    lines = _df_to_lines(df)
    rows_out: List[Dict[str, Any]] = []
    secao_atual = ''
    last_proc_idx: Optional[int] = None

    for line in lines:
        # --- descarta obs/rodapé (segunda guarda — já filtrado em _df_to_lines,
        #     mas pode aparecer concatenado)
        if _is_obs_line(line):
            continue

        # --- cabeçalho de tabela
        if _is_header_line(line):
            continue

        # --- título de seção
        if _is_secao_line(line):
            secao_atual = line.strip()
            last_proc_idx = None
            continue

        # --- linha de total
        if _is_total_line(line):
            moneys = _extract_money_values(line)
            rows_out.append({
                'Secao':        secao_atual,
                'Codigo':       '',
                'Procedimento': 'Total de Coparticipação',
                'Valor Unimed': '',
                'Copart 20%':   moneys[0] if len(moneys) > 0 else '',
                'Copart 30%':   moneys[1] if len(moneys) > 1 else '',
                'Copart 40%':   moneys[2] if len(moneys) > 2 else '',
                'Copart 50%':   moneys[3] if len(moneys) > 3 else '',
                'Tipo Linha':   'total',
            })
            last_proc_idx = None
            continue

        # --- linha de procedimento (começa com código)
        m_cod = _RE_COD_ANYWHERE.search(line)
        if m_cod and line.strip().startswith(m_cod.group()):
            cod = m_cod.group()
            rest = line[m_cod.end():].strip()
            moneys = _extract_money_values(rest)
            # isola texto do procedimento (sem os valores monetários)
            proc_text = _RE_MONEY_NUM.sub('', _RE_MONEY_BR.sub('', rest)).strip()
            proc_text = re.sub(r'\s{2,}', ' ', proc_text).strip()
            # limita a 5 valores: Valor Unimed + Copart 20-50%
            moneys = moneys[:5]
            rows_out.append({
                'Secao':        secao_atual,
                'Codigo':       cod,
                'Procedimento': proc_text,
                'Valor Unimed': moneys[0] if len(moneys) > 0 else '',
                'Copart 20%':   moneys[1] if len(moneys) > 1 else '',
                'Copart 30%':   moneys[2] if len(moneys) > 2 else '',
                'Copart 40%':   moneys[3] if len(moneys) > 3 else '',
                'Copart 50%':   moneys[4] if len(moneys) > 4 else '',
                'Tipo Linha':   'procedimento',
            })
            last_proc_idx = len(rows_out) - 1
            continue

        # --- possível continuação de procedimento
        moneys = _extract_money_values(line)
        if last_proc_idx is not None:
            r = rows_out[last_proc_idx]
            if not moneys:
                # puro texto: concatena ao procedimento anterior
                r['Procedimento'] = (r['Procedimento'] + ' ' + line.strip()).strip()
            else:
                # linha com valores que não chegaram na linha de código
                # (Camelot às vezes quebra assim)
                money5 = moneys[:5]
                if not r['Valor Unimed']:
                    r['Valor Unimed'] = money5[0] if money5 else ''
                    r['Copart 20%']   = money5[1] if len(money5) > 1 else r['Copart 20%']
                    r['Copart 30%']   = money5[2] if len(money5) > 2 else r['Copart 30%']
                    r['Copart 40%']   = money5[3] if len(money5) > 3 else r['Copart 40%']
                    r['Copart 50%']   = money5[4] if len(money5) > 4 else r['Copart 50%']
                else:
                    # complementa apenas colunas faltantes
                    cols_val = ['Copart 20%', 'Copart 30%', 'Copart 40%', 'Copart 50%']
                    mi = 0
                    for col in cols_val:
                        if not r[col] and mi < len(moneys):
                            r[col] = moneys[mi]
                            mi += 1
        # linha sem código e sem last_proc_idx → ignora

    if not rows_out:
        return pd.DataFrame(columns=OUT_COLS)

    result = pd.DataFrame(rows_out, columns=OUT_COLS)
    result = result[(result != '').any(axis=1)]
    return result


def _pdf_to_xlsx(in_pdf: str, out_dir: str) -> str:
    """Extrator no estilo 'modelo' ou retrocompat, controlado por env."""
    model_style = (os.environ.get("PDF_TO_XLSX_MODEL_STYLE", "0") == "1")
    enforce_pdf_page_limit(in_pdf, label="PDF de entrada")
    _prepare_camelot_env()

    import pandas as pd
    t_start = time.perf_counter()
    base = os.path.splitext(os.path.basename(in_pdf))[0]
    out_dir = os.path.abspath(out_dir); os.makedirs(out_dir, exist_ok=True)
    out_path = _unique_out_path(out_dir, base, "xlsx")

    # OCR se necessário
    src_pdf = in_pdf if _pdf_has_selectable_text(in_pdf) else _try_ocr(in_pdf)

    # ---- Extração
    dfs: List[pd.DataFrame] = []

    # SMART BBOX (lattice + hints)
    if os.environ.get("PDF_TO_XLSX_USE_SMART_BBOX","1") == "1":
        try:
            dfs = _extract_tables_smart(src_pdf)
            logger.debug("SMART encontrou %d tabelas", len(dfs))
        except Exception as e:
            logger.debug("SMART extraction falhou: %s", e)

    # Lattice global (todas as páginas)
    if not dfs:
        try:
            import camelot
            dpi = int(os.environ.get("PDF_TO_XLSX_DPI","200"))
            line_scale = int(os.environ.get("PDF_TO_XLSX_LINE_SCALE","80"))
            process_bg = int(os.environ.get("PDF_PROCESS_BACKGROUND","0")) == 1
            pages_arg = os.environ.get("PDF_PAGE_RANGE") or "all"
            tables = camelot.read_pdf(
                src_pdf, flavor="lattice", pages=pages_arg, line_scale=line_scale,
                strip_text="\n", process_background=process_bg, copy_text=["h","v"],
                shift_text=["l","t"], dpi=dpi
            )
            for t in getattr(tables, "tables", tables):
                if getattr(t, "df", None) is None or getattr(t.df, "empty", True): continue
                dfs.append(t.df)
        except Exception as e:
            logger.debug("PDF→XLSX: lattice global falhou: %s", e)

    # Lattice por áreas densas
    if (not dfs) or os.environ.get("PDF_TO_XLSX_ALWAYS_AREAS","1") == "1":
        try:
            import camelot
            dpi = int(os.environ.get("PDF_TO_XLSX_DPI","200"))
            line_scale = int(os.environ.get("PDF_TO_XLSX_LINE_SCALE","80"))
            process_bg = os.environ.get("PDF_PROCESS_BACKGROUND","0") == "1"
            areas_by_page = _find_dense_table_areas(src_pdf)
            for page_idx, areas in areas_by_page.items():
                for area in areas:
                    try:
                        tbs = camelot.read_pdf(
                            src_pdf, flavor="lattice", pages=str(page_idx), table_areas=[area],
                            line_scale=line_scale, strip_text="\n", process_background=process_bg,
                            copy_text=["h","v"], shift_text=["l","t"], dpi=dpi
                        )
                        for t in getattr(tbs, "tables", tbs):
                            if getattr(t, "df", None) is None or getattr(t.df, "empty", True): continue
                            dfs.append(t.df)
                    except Exception as ie:
                        logger.debug("Área %s falhou: %s", area, ie)
        except Exception as e:
            logger.debug("PDF→XLSX: lattice por áreas falhou: %s", e)

    # STREAM (último socorro, se habilitado)
    allow_stream_global = os.environ.get("PDF_TO_XLSX_ALLOW_STREAM","0") == "1"
    pages_arg = os.environ.get("PDF_PAGE_RANGE") or "all"

    def _dfs_have_rows(_dfs: List['pd.DataFrame']) -> bool:
        for raw in _dfs:
            cleaned, _ = _clean_and_infer(raw)
            if not cleaned.empty:
                return True
        return False

    if (not dfs or not _dfs_have_rows(dfs)) and allow_stream_global:
        try:
            rescued = _rescue_with_stream(src_pdf, pages_arg)
            if rescued:
                dfs = rescued
        except Exception as e:
            logger.debug("Rescue STREAM falhou: %s", e)

    # pdfplumber tables (fallback)
    if not dfs or not _dfs_have_rows(dfs):
        try:
            plumb = _pdfplumber_tables_dfs(src_pdf, pages_arg)
            if plumb:
                dfs = plumb
        except Exception as e:
            logger.debug("pdfplumber tables falhou: %s", e)

    # fallback texto 1-coluna
    if not dfs:
        try:
            import pdfplumber, pandas as _pd
            with pdfplumber.open(src_pdf) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    if lines:
                        dfs.append(_pd.DataFrame(lines, columns=["Texto"]))  # type: ignore
        except Exception as e:
            logger.debug("PDF→XLSX: fallback texto falhou: %s", e)

    # ---- Escrita XLSX
    try:
        import pandas as pd
        import xlsxwriter
        try:
            writer = pd.ExcelWriter(out_path, engine="xlsxwriter",
                                    engine_kwargs={"options": {"strings_to_urls": False}})
        except TypeError:
            writer = pd.ExcelWriter(out_path, engine="xlsxwriter")
    except Exception:
        import pandas as pd
        writer = pd.ExcelWriter(out_path, engine="openpyxl")

    metas: Dict[str, Dict[str, Any]] = {}
    wrote_any = False

    # MODEL STYLE: 1 tabela = 1 sheet ("Table N")
    if model_style:
        idx = 1
        copart_sheet_used = False
        for raw_df in dfs:
            df, meta = _clean_and_infer(raw_df)
            df = _drop_all_empty_rows(df)
            if df.empty:
                continue
            # --- Fase 1: normalização inteligente coparticipação ---
            sheet = f"Table {idx}"
            if _looks_like_coparticipacao_table(raw_df):
                try:
                    norm = _normalize_coparticipacao_table(raw_df)
                    if not norm.empty:
                        df = norm
                        meta = {}
                        sheet = "Coparticipacao" if not copart_sheet_used else f"Coparticipacao {idx}"
                        copart_sheet_used = True
                        logger.debug("PDF→XLSX model_style: normalização coparticipação aplicada (%s)", sheet)
                except Exception as _ne:
                    logger.debug("PDF→XLSX: normalização coparticipação falhou, usando df genérico: %s", _ne)
            df.to_excel(writer, index=False, header=True, sheet_name=sheet)
            metas[sheet] = meta
            idx += 1
            wrote_any = True

    else:
        # Retrocompatibilidade (com consolidação e/ou schema)
        target_schema = _load_target_schema_from_env()
        if target_schema:
            cleaned = []
            for raw_df in dfs:
                df, _meta = _clean_and_infer(raw_df)
                if not df.empty:
                    cleaned.append(df)
            big = pd.concat(cleaned, ignore_index=True, sort=False) if cleaned else None
            if big is not None and not big.empty:
                mapped, matched, total = _map_columns_to_schema_with_stats(big, target_schema)
                if matched >= max(4, int(0.4 * max(1,total))):
                    mapped = _drop_all_empty_rows(mapped)
                    if not mapped.empty:
                        sheet = "Dados"
                        mapped.to_excel(writer, index=False, header=True, sheet_name=sheet)
                        _, meta_tmp = _clean_and_infer(mapped.copy())
                        metas[sheet] = meta_tmp
                        wrote_any = True
            if not wrote_any and big is not None and not big.empty:
                big2 = _drop_all_empty_rows(big)
                if not big2.empty:
                    sheet = "Dados"
                    big2.to_excel(writer, index=False, header=True, sheet_name=sheet)
                    _, meta_tmp = _clean_and_infer(big2.copy())
                    metas[sheet] = meta_tmp
                    wrote_any = True
        else:
            single = os.environ.get("XLSX_SINGLE_SHEET","0") == "1"
            if single:
                cleaned = []
                for raw_df in dfs:
                    df, _ = _clean_and_infer(raw_df)
                    if not df.empty:
                        cleaned.append(df)
                big = pd.concat(cleaned, ignore_index=True, sort=False) if cleaned else None
                if big is not None and not big.empty:
                    big2 = _drop_all_empty_rows(big)
                    if not big2.empty:
                        sheet = "Dados"
                        big2.to_excel(writer, index=False, header=True, sheet_name=sheet)
                        _, meta_tmp = _clean_and_infer(big2.copy())
                        metas[sheet] = meta_tmp
                        wrote_any = True
            else:
                idx = 1
                copart_sheet_used = False
                for raw_df in dfs:
                    df, meta = _clean_and_infer(raw_df)
                    df = _drop_all_empty_rows(df)
                    if df.empty:
                        continue
                    # --- Fase 1: normalização inteligente coparticipação ---
                    sheet = f"Tabela {idx}"
                    if _looks_like_coparticipacao_table(raw_df):
                        try:
                            norm = _normalize_coparticipacao_table(raw_df)
                            if not norm.empty:
                                df = norm
                                meta = {}
                                sheet = "Coparticipacao" if not copart_sheet_used else f"Coparticipacao {idx}"
                                copart_sheet_used = True
                                logger.debug(
                                    "PDF→XLSX: normalização coparticipação aplicada (%s)", sheet
                                )
                        except Exception as _ne:
                            logger.debug(
                                "PDF→XLSX: normalização coparticipação falhou, usando df genérico: %s", _ne
                            )
                    df.to_excel(writer, index=False, header=True, sheet_name=sheet)
                    metas[sheet] = meta
                    idx += 1
                    wrote_any = True

    writer.close()

    if not wrote_any:
        logger.info(
            "PDF→XLSX: nenhuma tabela/texto detectado em '%s'. Tempo: %.2fs",
            os.path.basename(in_pdf), time.perf_counter() - t_start
        )
        # Remove arquivo vazio para não deixar lixo no disco
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            pass
        raise BadRequest(
            "Não foi possível extrair tabelas deste PDF. "
            "O arquivo pode ser escaneado, imagem ou não possuir estrutura tabular."
        )

    # Pós-formatação com openpyxl (larguras, filtros, números, Excel Table opcional)
    try:
        from openpyxl import load_workbook
        wb = load_workbook(out_path)
        for ws in wb.worksheets:
            _format_openpyxl_sheet(ws, metas.get(ws.title, {}))
        wb.save(out_path)
    except Exception as e:
        logger.debug("Formatação openpyxl falhou: %s", e)

    logger.info("Tempo PDF→XLSX total: %.2fs", time.perf_counter()-t_start)
    return out_path

# ---------------- PDF → CSV ----------------
FILTER_DOCX = "Office Open XML Text"
FILTER_CSV  = os.environ.get("CSV_FILTER_NAME", "Text - txt - csv (StarCalc)")
CSV_FILTER_OPTS = os.environ.get("CSV_FILTER_OPTS", "59,34,76,1")
FILTER_XLSM = "Calc MS Excel 2007 VBA XML"
FILTER_XLSX = "Calc MS Excel 2007 XML"

def _pdf_to_csv(in_pdf: str, out_dir: str) -> str:
    enforce_pdf_page_limit(in_pdf, label="PDF de entrada")
    _prepare_camelot_env()
    import pandas as pd, csv as _csv
    base = os.path.splitext(os.path.basename(in_pdf))[0]
    out_dir = os.path.abspath(out_dir); os.makedirs(out_dir, exist_ok=True)
    out_path = _unique_out_path(out_dir, base, "csv")
    dpi = int(os.environ.get("PDF_TO_XLSX_DPI","200"))
    line_scale = int(os.environ.get("PDF_TO_XLSX_LINE_SCALE","80"))
    process_bg = os.environ.get("PDF_PROCESS_BACKGROUND","0") == "1"
    pages_arg = os.environ.get("PDF_PAGE_RANGE") or "all"

    try:
        import camelot
        tables = camelot.read_pdf(in_pdf, flavor="lattice", pages=pages_arg, line_scale=line_scale,
                                  strip_text="\n", process_background=process_bg, copy_text=["h","v"],
                                  shift_text=["l","t"], dpi=dpi)
        if getattr(tables, 'n', 0) > 0:
            df = pd.concat([t.df for t in getattr(tables, 'tables', tables)],
                           ignore_index=True).map(_excel_safe_str)
            df.to_csv(out_path, index=False, header=False, encoding="utf-8")
            if os.path.getsize(out_path) > 0:
                return out_path
    except Exception as e:
        logger.debug("PDF→CSV: lattice falhou: %s", e)

    try:
        import camelot
        tables = camelot.read_pdf(in_pdf, flavor="stream", pages=pages_arg, strip_text="\n", dpi=dpi)
        if getattr(tables, 'n', 0) > 0:
            df = pd.concat([t.df for t in getattr(tables, 'tables', tables)],
                           ignore_index=True).map(_excel_safe_str)
            df.to_csv(out_path, index=False, header=False, encoding="utf-8")
            if os.path.getsize(out_path) > 0:
                return out_path
    except Exception as e:
        logger.debug("PDF→CSV: stream falhou: %s", e)

    try:
        import pdfplumber
        rows = []
        with pdfplumber.open(in_pdf) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    rows.extend([[ _excel_safe_str(c) for c in row ] for row in table])
        if rows:
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = _csv.writer(f); w.writerows(rows)
            if os.path.getsize(out_path) > 0:
                return out_path
    except Exception as e:
        logger.debug("PDF→CSV: pdfplumber tables falhou: %s", e)

    try:
        import pdfplumber
        with pdfplumber.open(in_pdf) as pdf, open(out_path, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    w.writerow([_excel_safe_str(line)])
        return out_path
    except Exception:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("Falha ao extrair conteúdo do PDF.\n")
        return out_path

# ============== Normalização de páginas (ATUALIZADO) ==============
def _papersize_token(name: str) -> str:
    n = (name or "A4").strip().lower()
    return n if n in ("a4", "letter") else "a4"

def _gs_autorotate_token(mode: str) -> str:
    mode = (mode or "none").strip().lower()
    if mode == "all":
        return "/All"
    if mode == "page":
        return "/PageByPage"
    return "/None"  # none

def normalize_pdf_pages(input_pdf: str, page_size: str = "A4", autorotate: str = "none") -> str:
    """
    Normaliza TODAS as páginas para A4/LETTER usando Ghostscript:
    -sPAPERSIZE=<a4|letter> -dFIXEDMEDIA -dPDFFitPage -dAutoRotatePages=<None|PageByPage|All>
    Retorna um **novo** caminho de saída.
    """
    token = _papersize_token(page_size)
    ar_token = _gs_autorotate_token(autorotate)
    root, ext = os.path.splitext(input_pdf)
    out_path = f"{root}_norm_{page_size.upper()}{ext or '.pdf'}"
    cmd = [
        GHOSTSCRIPT_BIN,
        "-sDEVICE=pdfwrite",
        f"-sPAPERSIZE={token}",
        "-dFIXEDMEDIA",
        "-dPDFFitPage",
        f"-dAutoRotatePages={ar_token}",
        "-dCompatibilityLevel=1.6",
        "-dNOPAUSE","-dBATCH","-dQUIET","-dSAFER",
        f"-sOutputFile={out_path}",
        input_pdf,
    ]
    logger.debug("[normalize_pdf_pages] %s", " ".join(cmd))
    run_in_sandbox(cmd, timeout=max(GHOSTSCRIPT_TIMEOUT, 60), cpu_seconds=60, mem_mb=768)
    return out_path

def _strip_page_rotate(in_pdf: str) -> str:
    """
    Zera o /Rotate de todas as páginas (se existir), gerando um novo PDF.
    Útil quando PDFs trazem rotação fixa gravada.
    """
    out_pdf = _tmp_out_path("pdf")
    try:
        try:
            from PyPDF2 import PdfReader, PdfWriter
        except Exception:
            from pypdf import PdfReader, PdfWriter
        reader = PdfReader(in_pdf)
        writer = PdfWriter()
        for pg in reader.pages:
            # remove explicit rotate if present
            if "/Rotate" in pg:
                try:
                    # pypdf >= 3
                    pg.rotate(0)  # no-op, but keeps API similar
                    del pg["/Rotate"]
                except Exception:
                    try:
                        del pg["/Rotate"]
                    except Exception:
                        pass
            writer.add_page(pg)
        with open(out_pdf, "wb") as fh:
            writer.write(fh)
        return out_pdf
    except Exception as e:
        logger.warning("Falha ao stripar /Rotate (%s). Retornando original.", e)
        try:
            os.remove(out_pdf)
        except Exception:
            pass
        return in_pdf

def _needs_normalization(in_pdf: str, page_size: str = "A4") -> bool:
    """
    Heurística: normalizar se:
      - houver páginas com tamanhos diferentes do alvo (A4/Letter), OU
      - houver mistura retrato/paisagem, OU
      - houver /Rotate explícito em qualquer página.
    """
    target = page_size.upper()
    tw, th = SIZES_PT.get(target, SIZES_PT["A4"])
    # tolerância em pontos
    tol = 2.0

    try:
        try:
            from PyPDF2 import PdfReader
        except Exception:
            from pypdf import PdfReader
        rdr = PdfReader(in_pdf)
        saw_portrait, saw_landscape = False, False
        for p in rdr.pages:
            mb = p.mediabox
            w = float(mb.right) - float(mb.left)
            h = float(mb.top) - float(mb.bottom)
            if abs(w - tw) > tol or abs(h - th) > tol:
                # aceita também A4 landscape (troca w/h)
                if not (abs(w - th) <= tol and abs(h - tw) <= tol):
                    return True
            if w >= h: saw_landscape = True
            else:      saw_portrait  = True
            # /Rotate explícito?
            try:
                if "/Rotate" in p and int(p["/Rotate"]) % 360 != 0:
                    return True
            except Exception:
                pass
        # se tiver mistura de orientações, ainda pode querer normalizar para A4 único
        return saw_portrait and saw_landscape
    except Exception as e:
        logger.debug("Falha ao inspecionar PDF (%s). Por segurança: normaliza.", e)
        return True

# ---------------- Dispatcher principal (mantido, agora usa o novo _image_to_pdf) ----------------
def convert_upload_to_target(upload_file, target: str, out_dir: str) -> str:
    target = target.lower().strip()
    if target not in {'pdf','docx','csv','xlsm','xlsx'}:
        raise BadRequest(f"Destino não suportado: {target}")

    name = upload_file.filename or 'arquivo'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    base = os.path.splitext(os.path.basename(name))[0] or 'arquivo'

    if target == 'pdf':
        if ext == 'pdf':
            in_path = _save_upload_to_tmp(upload_file, suffix='.pdf')
            try:
                enforce_pdf_page_limit(in_path, label="PDF de entrada")
                dst_path = _unique_out_path(out_dir, base, 'pdf'); shutil.move(in_path, dst_path)
                return dst_path
            finally:
                try: os.remove(in_path)
                except OSError: pass

        if ext in IMG_EXTS:
            in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext)
            tmp_pdf = _tmp_out_path('pdf')
            try:
                _image_to_pdf(in_path, tmp_pdf)   # ⬅️ A4/Letter + margens + auto-landscape + EXIF
                enforce_pdf_page_limit(tmp_pdf, label="PDF gerado")
                dst_path = _unique_out_path(out_dir, base, 'pdf'); shutil.move(tmp_pdf, dst_path)
                return dst_path
            finally:
                try: os.remove(in_path)
                except OSError: pass

        if ext in DOC_EXTS or ext in SHEET_EXTS:
            in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
            try:
                produced = _lo_convert(in_path, out_dir, 'pdf')
                enforce_pdf_page_limit(produced, label="PDF gerado")
                return produced
            finally:
                try: os.remove(in_path)
                except OSError: pass

        raise BadRequest(f'Extensão não suportada para conversão a PDF: {ext or "sem extensão"}')

    if target == 'docx':
        if ext in IMG_EXTS:
            raise BadRequest("Imagens não são convertidas para DOCX automaticamente.")
        in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
        try:
            if ext == 'pdf':
                return _pdf_to_docx(in_path, out_dir)
            return _lo_convert(in_path, out_dir, 'docx', filter_name=FILTER_DOCX)
        finally:
            try: os.remove(in_path)
            except OSError: pass

    if target == 'csv':
        in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
        try:
            if ext == 'pdf':
                return _pdf_to_csv(in_path, out_dir)
            if ext not in SHEET_EXTS:
                raise BadRequest("Apenas planilhas (xls/xlsx/ods/csv) ou PDF podem virar CSV.")
            return _lo_convert(in_path, out_dir, 'csv', filter_name=FILTER_CSV, filter_opts=CSV_FILTER_OPTS)
        finally:
            try: os.remove(in_path)
            except OSError: pass

    if target == 'xlsm':
        in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
        try:
            if ext == 'pdf':
                # Usa caminho de XLSX (rápido); caso necessário, aplique template XLSM fora daqui
                return _pdf_to_xlsx(in_path, out_dir)
            if ext not in SHEET_EXTS:
                raise BadRequest("Apenas PDF ou planilhas (xls/xlsx/ods/csv) podem virar XLSM.")
            return _lo_convert(in_path, out_dir, 'xlsm', filter_name=FILTER_XLSM)
        finally:
            try: os.remove(in_path)
            except OSError: pass

    if target == 'xlsx':
        in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
        try:
            if ext == 'pdf':
                return _pdf_to_xlsx(in_path, out_dir)
            if ext in SHEET_EXTS:
                return _lo_convert(in_path, out_dir, 'xlsx', filter_name=FILTER_XLSX)
            raise BadRequest("Apenas PDF ou planilhas (xls/xlsx/ods/csv) podem virar XLSX.")
        finally:
            try: os.remove(in_path)
            except OSError: pass

    raise BadRequest(f"Destino não suportado: {target}")

# ---------------- Legacy compat (usa o novo _image_to_pdf) ----------------
def converter_doc_para_pdf(upload_file, modificacoes=None) -> str:
    """Compat antigo: converte qualquer documento/imagem para PDF."""
    name = upload_file.filename or 'arquivo'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
    out_path = _tmp_out_path('pdf')
    try:
        if ext == 'pdf':
            shutil.move(in_path, out_path)
            enforce_pdf_page_limit(out_path, label="PDF de entrada")
            return out_path
        if ext in IMG_EXTS:
            _image_to_pdf(in_path, out_path)  # ⬅️ A4/Letter + EXIF agora
        elif ext in DOC_EXTS or ext in SHEET_EXTS:
            produced = _lo_convert(in_path, os.path.dirname(out_path), 'pdf')
            if os.path.abspath(produced) != os.path.abspath(out_path):
                if os.path.exists(out_path):
                    os.remove(out_path)
                shutil.move(produced, out_path)
        else:
            raise BadRequest(f'Extensão não suportada para este conversor: {ext or "sem extensão"}')
        enforce_pdf_page_limit(out_path, label="PDF gerado")
        return out_path
    finally:
        try: os.remove(in_path)
        except OSError: pass

def converter_planilha_para_pdf(upload_file, modificacoes=None) -> str:
    """Compat antigo: planilhas (xls/xlsx/ods/csv) → PDF via LibreOffice."""
    name = upload_file.filename or 'planilha'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    in_path = _save_upload_to_tmp(upload_file, suffix='.' + ext if ext else '')
    out_path = _tmp_out_path('pdf')
    try:
        produced = _lo_convert(in_path, os.path.dirname(out_path), 'pdf')
        if os.path.abspath(produced) != os.path.abspath(out_path):
            if os.path.exists(out_path):
                os.remove(out_path)
            shutil.move(produced, out_path)
        enforce_pdf_page_limit(out_path, label="PDF gerado")
        return out_path
    finally:
        try: os.remove(in_path)
        except OSError: pass

# ---------------- Multi-file (compat) ----------------
def convert_many_uploads(files, target: str, out_dir: str):
    outputs = []
    os.makedirs(out_dir, exist_ok=True)
    for up in files:
        outputs.append(convert_upload_to_target(up, target, out_dir))
    return outputs

# --- Unir vários uploads em UM PDF (normaliza A4 por padrão) ---
def convert_many_uploads_to_single_pdf(
    uploads: List,
    workdir: str | None = None,
    *,
    normalize: str = None,  # "auto"|"on"|"off" ; se None, usa MERGE_NORMALIZE_MODE
    norm_page_size: str = "A4",
) -> str:
    if not uploads:
        raise ValueError("Nenhum arquivo enviado.")

    try:
        from PyPDF2 import PdfMerger  # pip install PyPDF2
    except Exception:
        from pypdf import PdfMerger   # pip install pypdf

    out_dir = os.path.abspath(workdir) if workdir else tempfile.mkdtemp(prefix="gvpdf_merge_")
    os.makedirs(out_dir, exist_ok=True)

    pdf_paths = convert_many_uploads(uploads, 'pdf', out_dir)
    pdf_paths = [p for p in (pdf_paths or []) if p and os.path.isfile(p)]
    if not pdf_paths:
        raise RuntimeError("Conversão não gerou PDFs.")

    final_path = _unique_out_path(out_dir, "arquivos_unidos", "pdf")
    merger = PdfMerger()
    try:
        for p in pdf_paths:
            merger.append(p)
        with open(final_path, "wb") as fh:
            merger.write(fh)
    finally:
        merger.close()

    # (opcional) remove /Rotate das páginas antes de normalizar
    merged_path = final_path
    if MERGE_STRIP_ROTATE:
        stripped = _strip_page_rotate(final_path)
        if stripped != final_path:
            try: os.remove(final_path)
            except OSError: pass
            merged_path = stripped

    # Decide normalização
    norm_mode = (normalize or MERGE_NORMALIZE_MODE or "auto").lower()
    if norm_mode != "off":
        try:
            if norm_mode == "always" or (norm_mode == "auto" and _needs_normalization(merged_path, norm_page_size)):
                normalized = normalize_pdf_pages(merged_path, norm_page_size, autorotate=MERGE_NORMALIZE_AUTOROTATE)
                try: os.remove(merged_path)
                except OSError: pass
                merged_path = normalized
        except Exception as e:
            logger.warning("Normalização falhou (%s); retornando merge bruto.", e)

    # ===== (7.1) MÉTRICAS: registrar 'convert' (multi-upload → 1 PDF) =====
    try:
        # bytes_in ~ soma dos PDFs gerados a partir dos uploads
        bytes_in = 0
        for p in pdf_paths:
            try:
                bytes_in += os.path.getsize(p)
            except Exception:
                pass
        # se houver contexto de request e Content-Length maior, usar como melhor estimativa
        if has_request_context():
            try:
                cl = int(request.content_length or 0)
                if cl > bytes_in:
                    bytes_in = cl
            except Exception:
                pass
        bytes_out = os.path.getsize(merged_path) if os.path.exists(merged_path) else None
        record_job_event(
            route="/api/convert/merge-a4",  # chama /convert/merge-a4 (e /to-pdf-merge alias)
            action="convert",
            bytes_in=(bytes_in if bytes_in > 0 else None),
            bytes_out=bytes_out,
            files_out=1,
        )
    except Exception:
        pass
    # =====================================================================

    return merged_path