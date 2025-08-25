# -*- coding: utf-8 -*-
"""
converter_service.py — PDF → XLSX no estilo “modelo”
- 1 tabela = 1 planilha ("Table 1", "Table 2", ...)
- Lattice-first com detecção de áreas; STREAM só como último socorro (opcional via env)
- Cabeçalhos limpos/curtos (<=80 chars) e nomes únicos
- NÃO cria Excel Table por padrão (evita alerta de "reparar arquivo"); pode reativar via XLSX_ADD_TABLE=1
- OCR opcional (OCR_ON_PDF_TO_XLSX=1) se o PDF for imagem
- Mantém os demais conversores (PDF↔DOCX/CSV/XLSM/XLSX) e os limites de páginas/segurança
"""
from __future__ import annotations

import os, re, tempfile, subprocess, shutil, logging, time
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Dict, Any, Tuple

from PIL import Image
from werkzeug.exceptions import BadRequest

from ..utils.limits import enforce_pdf_page_limit

logger = logging.getLogger(__name__)

IMG_EXTS   = {'jpg','jpeg','png','bmp','tif','tiff'}
DOC_EXTS   = {'doc','docx','odt','rtf','txt','html','htm','ppt','pptx','odp'}
SHEET_EXTS = {'csv','xls','xlsx','ods'}

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

# ---------------- Imagem → PDF ----------------
def _image_to_pdf(in_path: str, out_path: str) -> None:
    img = Image.open(in_path)
    try:
        if (img.format or '').upper() in ('TIFF', 'TIF'):
            pages = []
            try:
                i = 0
                while True:
                    img.seek(i); pages.append(img.convert('RGB')); i += 1
            except EOFError:
                pass
            if not pages:
                raise ValueError("TIFF vazio")
            pages[0].save(out_path, save_all=True, append_images=pages[1:], format='PDF', resolution=300.0)
        else:
            if img.mode in ('RGBA','P'): img = img.convert('RGB')
            img.save(out_path, 'PDF', resolution=300.0)
    finally:
        img.close()

# ---------------- LibreOffice helpers ----------------
def _soffice_bin() -> str:
    bin_cfg = os.environ.get('LIBREOFFICE_BIN') or os.environ.get('SOFFICE_BIN') or 'soffice'
    if os.name == 'nt' and bin_cfg.lower() == 'soffice':  # prefer .com no Windows
        return 'soffice.com'
    return bin_cfg

def _lo_convert(in_path: str, out_dir: str, out_ext: str,
                filter_name: Optional[str] = None, filter_opts: Optional[str] = None) -> str:
    convert_to = out_ext
    if filter_name and filter_opts:
        convert_to = f"{out_ext}:{filter_name}:{filter_opts}"
    elif filter_name:
        convert_to = f"{out_ext}:{filter_name}"

    lo_timeout = int(os.environ.get("LO_CONVERT_TIMEOUT_SEC", "120"))
    cmd = [
        _soffice_bin(),
        '--headless','--safe-mode','--nologo','--nodefault','--nolockcheck','--invisible',
        '--convert-to', convert_to, '--outdir', out_dir, in_path
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False, timeout=lo_timeout)
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

# ---------------- Excel helpers ----------------
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
    """Corta para <=80 chars e assegura unicidade."""
    out: List[str] = []
    used: set[str] = set()
    for h in headers:
        s = str(h or "").strip()
        if len(s) > max_len:
            s = s[:max_len-1] + "…"
        base = s or "Coluna"
        cand = base; i = 2
        while cand in used:
            cand = f"{base} ({i})"; i += 1
        used.add(cand); out.append(cand)
    return out

def _clean_and_infer(df):
    """Limpa vazios, escolhe melhor linha como header, infere tipos básicos e normaliza nomes."""
    import pandas as pd
    df = df.copy().map(lambda x: "" if x is None else str(x).replace("\r"," ").replace("\n"," ").strip())
    # drop colunas/linhas totalmente vazias
    df = df.loc[:, (df != "").any(axis=0)]
    df = df[(df != "").any(axis=1)]
    if df.empty: return df, {}

    # detecta a melhor linha de cabeçalho (maior preenchimento)
    header_idx, best_fill = 0, -1.0
    for i, row in df.iterrows():
        non_empty = (row != "").sum()
        fill = non_empty / max(1, len(row))
        if fill > best_fill and non_empty >= 2:
            best_fill, header_idx = fill, i
        if fill >= 0.7:  # bom o suficiente
            header_idx = i; break

    header = [h if h else f"Coluna {j+1}" for j, h in enumerate(list(df.iloc[header_idx].values))]
    header = _trim_headers(_make_unique_columns([str(h) for h in header]), max_len=80)
    df = df.iloc[header_idx+1:].reset_index(drop=True)
    df.columns = header

    # remove linhas que repetem o cabeçalho (artefato comum)
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

    # remove colunas totalmente NA após inferência
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

# ---------------- Alvos/schema (retrocompat; ignorado no MODEL_STYLE=1) ----------------
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

# ---------------- Detecção de áreas/colunas ----------------
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
    """Lattice com recorte por área + heurística de colunas; STREAM só se habilitado."""
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

            # ignora páginas “mensagens” / resumo
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
    """Varre retângulos/linhas do pdfplumber e sugere áreas densas para o Camelot."""
    import pdfplumber
    areas_by_page: Dict[int, List[str]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, p in enumerate(pdf.pages, start=1):
            W, H = p.width, p.height
            lines, rects = p.lines or [], p.rects or []
            candidates = []
            for r in rects:
                w = abs(r["x1"] - r["x0"]); h = abs(r["y1"] - r["y0"])
                if w*h < (W*H*0.05):  # descarta retângulos muito pequenos
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
    """Formatação leve; Excel Table opcional via XLSX_ADD_TABLE=1."""
    from openpyxl.styles import Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    # cabeçalho
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    max_col, max_row = ws.max_column, ws.max_row
    if max_col and max_row:
        ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"

    # formatos por coluna
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

    # largura de colunas
    for j in range(1, max_col+1):
        col_letter = get_column_letter(j); max_len = 0
        for r in range(1, max_row+1):
            v = ws[f"{col_letter}{r}"].value
            if v is None: continue
            s = str(v) + ("   " if r == 1 else "")
            max_len = max(max_len, len(s))
        ws.column_dimensions[col_letter].width = max(10, min(60, int(max_len*1.15)))

    # Excel Table (opcional)
    add_table = os.environ.get("XLSX_ADD_TABLE","0") == "1"
    if add_table and max_row >= 2 and max_col >= 1:
        import uuid as _uuid
        tbl_name = f"Tbl_{_uuid.uuid4().hex[:8]}"
        ref = f"A1:{get_column_letter(max_col)}{max_row}"
        table = Table(displayName=tbl_name, ref=ref)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2",
                                             showRowStripes=True, showColumnStripes=False)
        ws.add_table(table)

    # bordas finas
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
    """Mantido para retrocompat (não usado no MODEL_STYLE)."""
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
            process_bg = os.environ.get("PDF_PROCESS_BACKGROUND","0") == "1"
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
        for raw_df in dfs:
            df, meta = _clean_and_infer(raw_df)
            df = _drop_all_empty_rows(df)
            if df.empty:
                continue
            sheet = f"Table {idx}"
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
                for raw_df in dfs:
                    df, meta = _clean_and_infer(raw_df)
                    df = _drop_all_empty_rows(df)
                    if df.empty:
                        continue
                    sheet = f"Tabela {idx}"
                    df.to_excel(writer, index=False, header=True, sheet_name=sheet)
                    metas[sheet] = meta
                    idx += 1
                    wrote_any = True

    writer.close()

    if not wrote_any:
        _write_minimal_xlsx(out_path, "Nenhuma tabela detectada. Tente aumentar LINE_SCALE/DPI ou ligar o OCR.")
        logger.info("Tempo PDF→XLSX total: %.2fs", time.perf_counter()-t_start)
        return out_path

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

    # pdfplumber tables
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

    # texto linha-a-linha
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

# ---------------- Dispatcher principal ----------------
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
                _image_to_pdf(in_path, tmp_pdf)
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

# ---------------- Legacy compat ----------------
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
            _image_to_pdf(in_path, out_path)
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
    """
    API compatível com as rotas: converte vários uploads para o mesmo destino.
    Mantém o comportamento do convert_upload_to_target, só itera a lista.
    """
    outputs = []
    os.makedirs(out_dir, exist_ok=True)
    for up in files:
        outputs.append(convert_upload_to_target(up, target, out_dir))
    return outputs
# --- APPEND: unir vários uploads em UM PDF na ordem recebida -----------------
from typing import List

def convert_many_uploads_to_single_pdf(uploads: List, workdir: str | None = None) -> str:
    """
    Converte N uploads para PDF e mescla em um único PDF final, preservando a ORDEM.
    Retorna o caminho do PDF final (para usar com send_file).
    Depende de: convert_many_uploads(uploads, 'pdf', out_dir) já definido acima.
    """
    if not uploads:
        raise ValueError("Nenhum arquivo enviado.")

    # Import local para não exigir a lib caso a função não seja utilizada
    try:
        from PyPDF2 import PdfMerger  # pip install PyPDF2
    except Exception:  # fallback
        from pypdf import PdfMerger   # pip install pypdf

    # 1) Diretório de trabalho
    out_dir = os.path.abspath(workdir) if workdir else tempfile.mkdtemp(prefix="gvpdf_merge_")
    os.makedirs(out_dir, exist_ok=True)

    # 2) Converte cada upload -> PDF (respeita ordem do 'uploads')
    pdf_paths = convert_many_uploads(uploads, 'pdf', out_dir)
    pdf_paths = [p for p in (pdf_paths or []) if p and os.path.isfile(p)]
    if not pdf_paths:
        raise RuntimeError("Conversão não gerou PDFs.")

    # 3) Mescla em um único arquivo
    final_path = _unique_out_path(out_dir, "arquivos_unidos", "pdf")
    merger = PdfMerger()
    try:
        for p in pdf_paths:
            merger.append(p)
        with open(final_path, "wb") as fh:
            merger.write(fh)
    finally:
        merger.close()

    return final_path
# ---------------------------------------------------------------------------