# app/utils/pdf_utils.py
# -*- coding: utf-8 -*-
import os, re, hashlib
from copy import deepcopy
from typing import List, Dict, Optional, Union, Any
from PIL import Image
import pypdfium2 as pdfium
from PyPDF2 import PdfReader, PdfWriter
from flask import current_app
from werkzeug.exceptions import BadRequest

THUMB_MAX_WIDTH = 512
THUMBS_SUBDIR   = "_thumbs"
SAFE_NAME_RE    = re.compile(r"^[a-f0-9]{16,64}$")

# ---------- Thumbs ----------
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _thumbs_dir() -> str:
    base = current_app.config.get("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
    thumbs = os.path.join(base, THUMBS_SUBDIR)
    os.makedirs(thumbs, exist_ok=True)
    return thumbs

def _render_first_page_to_png(src_pdf_path: str, out_png_path: str, dpi: int = 144) -> None:
    pdf = pdfium.PdfDocument(src_pdf_path)
    if len(pdf) < 1:
        pdf.close()
        raise ValueError("PDF sem páginas.")
    page = pdf.get_page(0)
    try:
        bitmap = page.render(scale=(dpi / 72.0))
        pil = bitmap.to_pil()
        if pil.width > THUMB_MAX_WIDTH:
            new_h = int(pil.height * (THUMB_MAX_WIDTH / pil.width))
            pil = pil.resize((THUMB_MAX_WIDTH, new_h), Image.LANCZOS)
        pil.save(out_png_path, format="PNG", optimize=True)
    finally:
        page.close(); pdf.close()

def preview_pdf(abs_pdf_path: str) -> dict:
    if not os.path.exists(abs_pdf_path): raise FileNotFoundError("Arquivo PDF não encontrado.")
    full_hash = _sha256_file(abs_pdf_path)
    thumb_id = full_hash[:40]
    if not SAFE_NAME_RE.match(thumb_id): raise ValueError("Identificador de miniatura inválido.")
    thumbs_dir = _thumbs_dir()
    thumb_filename = f"{thumb_id}.png"
    thumb_path = os.path.join(thumbs_dir, thumb_filename)
    if not os.path.exists(thumb_path): _render_first_page_to_png(abs_pdf_path, thumb_path)
    return {"thumb_id": thumb_id, "thumb_path": thumb_path, "filename": thumb_filename}

# ---------- Core de recorte/rotação ----------
def _safe_box(llx, lly, urx, ury, pw, ph):
    llx = max(0.0, min(llx, pw)); lly = max(0.0, min(lly, ph))
    urx = max(0.0, min(urx, pw)); ury = max(0.0, min(ury, ph))
    if urx < llx: urx, llx = llx, urx
    if ury < lly: ury, lly = lly, ury
    return llx, lly, urx, ury

def _apply_rect_to_page(page_obj, llx, lly, urx, ury):
    pw = float(page_obj.mediabox.width); ph = float(page_obj.mediabox.height)
    llx, lly, urx, ury = _safe_box(llx, lly, urx, ury, pw, ph)
    if (urx-llx) <= 1.0 or (ury-lly) <= 1.0: return
    page_obj.mediabox.lower_left  = (llx, lly)
    page_obj.mediabox.upper_right = (urx, ury)
    try:
        page_obj.cropbox.lower_left  = (llx, lly)
        page_obj.cropbox.upper_right = (urx, ury)
    except Exception:
        pass

def _apply_crop_from_spec(page_obj, spec: Union[List[float], Dict[str, Any]]) -> None:
    if not spec: return
    if isinstance(spec, dict) and 'crop' in spec and isinstance(spec['crop'], dict):
        spec = spec['crop']

    mb = page_obj.mediabox
    pw = float(mb.width); ph = float(mb.height)

    if isinstance(spec, (list, tuple)) and len(spec) == 4:
        x0,y0,x1,y1 = spec
        if 0<=x0<=1 and 0<=y0<=1 and 0<=x1<=1 and 0<=y1<=1:
            llx = min(x0,x1)*pw; lly = min(y0,y1)*ph; urx = max(x0,x1)*pw; ury = max(y0,y1)*ph
        else:
            llx,lly = min(x0,x1), min(y0,y1); urx,ury = max(x0,x1), max(y0,y1)
        _apply_rect_to_page(page_obj, llx,lly,urx,ury); return

    if isinstance(spec, dict):
        try:
            x=float(spec.get('x',0)); y=float(spec.get('y',0)); w=float(spec.get('w',0)); h=float(spec.get('h',0))
        except Exception:
            return
        unit=(spec.get('unit') or '').lower().strip()
        origin=(spec.get('origin') or 'bottomleft').lower().strip()
        if not unit and 0<=x<=1 and 0<=y<=1 and 0<w<=1 and 0<h<=1: unit='percent'

        if unit=='percent':
            if origin=='topleft':
                llx = x*pw; lly = (1.0-(y+h))*ph
            else:
                llx = x*pw; lly = y*ph
            urx = llx + w*pw; ury = lly + h*ph
        else:
            if origin=='topleft':
                llx = x; lly = ph-(y+h)
            else:
                llx = x; lly = y
            urx = llx + w; ury = lly + h
        _apply_rect_to_page(page_obj, llx,lly,urx,ury)

def _apply_mods_to_page(page_obj, modifications: Optional[Dict[str, Any]] = None, **kwargs):
    mods = modifications or kwargs.get('modificacoes')
    if not isinstance(mods, dict): return page_obj
    try:
      deg = int(mods.get('rotate', 0)) % 360
      if deg:
        if hasattr(page_obj,'rotate'): page_obj.rotate(deg)
        elif hasattr(page_obj,'rotate_clockwise'): page_obj.rotate_clockwise(deg)
    except Exception:
      pass
    if 'crop' in mods:
      try: _apply_crop_from_spec(page_obj, mods['crop'])
      except Exception: pass
    return page_obj

# ---------- Pipeline (arquivo) ----------
def _pipeline_file(input_path: str, output_path: str, pages: List[int],
                   rotations: Optional[Dict[str,int]]=None,
                   crops: Optional[Dict[str,Any]]=None) -> dict:
    if not pages: raise BadRequest("Nenhuma página selecionada.")

    cfg = getattr(current_app, "config", {})
    max_pdf_pages = int(cfg.get("MAX_PDF_PAGES", os.getenv("MAX_PDF_PAGES", "500")))
    max_total_pages = int(cfg.get("MAX_TOTAL_PAGES", os.getenv("MAX_TOTAL_PAGES", "1000")))

    try:
        reader = PdfReader(input_path)
    except Exception:
        raise BadRequest("Não foi possível ler o PDF. O arquivo pode estar corrompido.")
    if getattr(reader, "is_encrypted", False):
        raise BadRequest("Arquivo protegido por senha. Remova a senha localmente e tente novamente.")

    n = len(reader.pages)
    if n == 0: raise BadRequest("PDF vazio.")
    if n > max_pdf_pages: raise BadRequest(f"PDF excede o limite de páginas ({n} > {max_pdf_pages}).")
    if len(pages) > max_total_pages: raise BadRequest(f"Seleção excede o limite total de páginas ({len(pages)} > {max_total_pages}).")
    for i in pages:
        if not isinstance(i,int) or i<1 or i>n: raise BadRequest(f"Página inválida na seleção: {i} (válido: 1..{n}).")

    rot_map = {}
    for k,v in (rotations or {}).items():
        try:
            kk = int(k); vv = int(v)%360
            if vv in (0,90,180,270): rot_map[kk]=vv
        except: pass

    crops_map = {}
    if isinstance(crops, dict):
        for k,v in crops.items():
            try: crops_map[int(k)] = v
            except: pass

    writer = PdfWriter(); rotated=cropped=0
    for p in pages:
        page = deepcopy(reader.pages[p-1])
        if rot_map.get(p,0):
            try:
                if hasattr(page,'rotate'): page.rotate(rot_map[p])
                else: page.rotate_clockwise(rot_map[p])
                if rot_map[p]%360: rotated += 1
            except: pass
        if p in crops_map:
            before = (float(page.mediabox.width), float(page.mediabox.height))
            try:
                _apply_crop_from_spec(page, crops_map[p])
                after  = (float(page.mediabox.width), float(page.mediabox.height))
                if after != before: cropped += 1
            except: pass
        writer.add_page(page)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path,'wb') as fp: writer.write(fp)
    return {"pages_in": n, "pages_out": len(pages), "rotated_count": rotated, "cropped_count": cropped, "output_path": output_path}

# ---------- API pública (dispatcher) ----------
def apply_pdf_modifications(page_or_input_path,
                            output_path: Optional[str]=None,
                            pages: Optional[List[int]]=None,
                            rotations: Optional[Dict[str,int]]=None,
                            crops: Optional[Dict[str,Any]]=None,
                            strict: bool=True, **kwargs):
    """
    Dispatcher:
      - PageObject: apply_pdf_modifications(page, modificacoes={...})
      - Arquivo:    apply_pdf_modifications(input_path, output_path, pages=[...], rotations={...}, crops={...})
    """
    # modo PÁGINA
    if hasattr(page_or_input_path, "mediabox"):
        mods = kwargs.get('modifications') or kwargs.get('modificacoes') or {}
        return _apply_mods_to_page(page_or_input_path, modifications=mods)

    # modo ARQUIVO
    input_path = page_or_input_path
    if not isinstance(input_path, str) or not isinstance(output_path, str):
        raise BadRequest("Parâmetros inválidos para edição de arquivo (input/output).")
    if crops is None: crops = kwargs.get('modificacoes') or kwargs.get('mods')
    if rotations is None: rotations = kwargs.get('rotacoes')
    return _pipeline_file(input_path, output_path, pages or [], rotations, crops)