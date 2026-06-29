# app/utils/pdf_utils.py
# -*- coding: utf-8 -*-
import os, re, hashlib
from copy import deepcopy
from typing import List, Dict, Optional, Union, Any, Callable
from PIL import Image
import pikepdf
import pypdfium2 as pdfium
from PyPDF2 import PdfReader, PdfWriter
from flask import current_app
from werkzeug.exceptions import BadRequest

THUMB_MAX_WIDTH = 512
THUMBS_SUBDIR   = "_thumbs"
SAFE_NAME_RE    = re.compile(r"^[a-f0-9]{16,64}$")

PDF_PRESERVATION_WARNING = (
    "Compressao pesada ignorada para preservar formularios, anotacoes ou assinaturas visuais."
)
PDF_SIGNATURE_REWRITE_WARNING = (
    "A aparencia visual foi preservada, mas qualquer regravacao pode invalidar "
    "a assinatura digital criptografica."
)
PDF_RESIZE_IGNORED_WARNING = (
    "resize_to_a4 ignorado para preservar conteudo interativo."
)

_SAFE_ACROFORM_KEYS = (
    "/DR",
    "/DA",
    "/Q",
    "/NeedAppearances",
    "/SigFlags",
)

_UNSAFE_FORM_KEYS = (
    "/A",
    "/AA",
    "/JS",
    "/JavaScript",
    "/OpenAction",
    "/XFA",
)


def _is_path_inside_dir(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def _iter_upload_owned_files(paths, upload_folder: str):
    try:
        base_real = os.path.normcase(os.path.realpath(os.fspath(upload_folder)))
        cleanup_paths = tuple(paths or ())
    except (TypeError, ValueError, OSError):
        return

    for raw_path in cleanup_paths:
        if not raw_path:
            continue
        try:
            original = os.path.abspath(os.fspath(raw_path))
            resolved = os.path.normcase(os.path.realpath(original))
            if resolved == base_real:
                continue
            if not _is_path_inside_dir(base_real, resolved):
                continue
            yield original
        except (TypeError, ValueError, OSError):
            continue


def cleanup_upload_files(paths, upload_folder: str) -> None:
    """Best-effort removal of files contained by UPLOAD_FOLDER."""
    for path in _iter_upload_owned_files(paths, upload_folder):
        try:
            os.remove(path)
        except OSError:
            pass


def register_response_file_cleanup(response, paths, upload_folder: str):
    """
    Remove temporary response files only after the response iterable is closed.

    The containment check validates the resolved real path so symlinks or path
    tricks cannot remove files outside UPLOAD_FOLDER. Paths and UUIDs are
    intentionally not logged.
    """
    cleanup_paths = tuple(paths or ())

    def _cleanup_response_files() -> None:
        cleanup_upload_files(cleanup_paths, upload_folder)

    response.call_on_close(_cleanup_response_files)
    return response


def _obj_key(obj):
    objgen = getattr(obj, "objgen", None)
    if objgen and objgen != (0, 0):
        return ("obj", objgen[0], objgen[1])
    return ("mem", id(obj))


def _append_unique(items: list, keys: set, obj) -> None:
    key = _obj_key(obj)
    if key in keys:
        return
    keys.add(key)
    items.append(obj)


def _strip_unsafe_form_keys(obj) -> None:
    if not hasattr(obj, "keys"):
        return
    for key in _UNSAFE_FORM_KEYS:
        if key in obj:
            try:
                del obj[key]
            except Exception:
                pass
    if "/DR" in obj:
        dr = obj["/DR"]
        if hasattr(dr, "keys") and "/JavaScript" in dr:
            try:
                del dr["/JavaScript"]
            except Exception:
                pass


def _strip_catalog_danger(root) -> None:
    for key in ("/OpenAction", "/AA", "/JavaScript", "/JS"):
        if key in root:
            del root[key]
    if "/Names" not in root:
        return
    names = root["/Names"]
    for key in ("/JavaScript", "/EmbeddedFiles"):
        if key in names:
            del names[key]


def _copy_acroform_value(pdf_src: pikepdf.Pdf, pdf_dst: pikepdf.Pdf, value):
    if isinstance(value, (bool, int, float)):
        return value
    try:
        if getattr(value, "is_indirect", False):
            return pdf_dst.copy_foreign(value)
        return pdf_dst.copy_foreign(pdf_src.make_indirect(value))
    except Exception:
        return value


def _copy_safe_acroform_globals(pdf_src: pikepdf.Pdf, pdf_dst: pikepdf.Pdf) -> pikepdf.Dictionary:
    acroform = pikepdf.Dictionary()
    source_acroform = pdf_src.Root.get("/AcroForm")
    if not source_acroform:
        return acroform

    for key in _SAFE_ACROFORM_KEYS:
        if key not in source_acroform:
            continue
        acroform[key] = _copy_acroform_value(pdf_src, pdf_dst, source_acroform[key])

    _strip_unsafe_form_keys(acroform)
    return acroform


def _iter_page_annots(page: pikepdf.Page):
    for annot in list(page.get("/Annots", pikepdf.Array())):
        if hasattr(annot, "keys"):
            yield annot


def _iter_page_widgets(page: pikepdf.Page):
    for annot in _iter_page_annots(page):
        if str(annot.get("/Subtype", "")) == "/Widget":
            yield annot


def _field_chain_for_widget(widget) -> list:
    chain = [widget]
    seen = {_obj_key(widget)}
    current = widget

    while "/Parent" in current:
        parent = current["/Parent"]
        parent_key = _obj_key(parent)
        if parent_key in seen or not hasattr(parent, "keys"):
            del current["/Parent"]
            break
        chain.append(parent)
        seen.add(parent_key)
        current = parent

    return chain


def _field_name(field) -> str:
    value = field.get("/T")
    return str(value) if value is not None else ""


def _field_type(field) -> str:
    value = field.get("/FT")
    return str(value) if value is not None else ""


def _rename_duplicate_root_fields(root_fields: list) -> None:
    counts: Dict[str, int] = {}
    for field in root_fields:
        name = _field_name(field)
        if not name:
            continue
        count = counts.get(name, 0) + 1
        counts[name] = count
        if count > 1:
            field["/T"] = pikepdf.String(f"{name}__copy_{count}")


def _walk_fields(field, seen: Optional[set] = None):
    seen = seen or set()
    key = _obj_key(field)
    if key in seen or not hasattr(field, "keys"):
        return
    seen.add(key)
    yield field
    for kid in list(field.get("/Kids", pikepdf.Array())):
        yield from _walk_fields(kid, seen)


def _filtered_calculation_order(pdf_src: pikepdf.Pdf, fields_by_original_name: dict) -> pikepdf.Array:
    source_acroform = pdf_src.Root.get("/AcroForm")
    if not source_acroform or "/CO" not in source_acroform:
        return pikepdf.Array()

    calculation_order = pikepdf.Array()
    for source_field in source_acroform["/CO"]:
        source_name = _field_name(source_field)
        if not source_name:
            continue
        for exported_field in fields_by_original_name.get(source_name, []):
            calculation_order.append(exported_field)
    return calculation_order


def _rebuild_acroform_for_output(pdf_src: pikepdf.Pdf, pdf_dst: pikepdf.Pdf) -> None:
    """Rebuild /AcroForm using only widgets present in copied pages."""
    _strip_catalog_danger(pdf_dst.Root)

    root_fields = []
    root_field_keys = set()
    objects_by_key = {}
    children_by_parent = {}
    child_keys_by_parent = {}

    for page in pdf_dst.pages:
        for widget in _iter_page_widgets(page):
            widget["/P"] = page.obj
            chain = _field_chain_for_widget(widget)
            for obj in chain:
                _strip_unsafe_form_keys(obj)
                objects_by_key[_obj_key(obj)] = obj

            if len(chain) == 1:
                _append_unique(root_fields, root_field_keys, widget)
                continue

            for idx, child in enumerate(chain[:-1]):
                parent = chain[idx + 1]
                parent_key = _obj_key(parent)
                children = children_by_parent.setdefault(parent_key, [])
                child_keys = child_keys_by_parent.setdefault(parent_key, set())
                _append_unique(children, child_keys, child)

            _append_unique(root_fields, root_field_keys, chain[-1])

    if not root_fields:
        if "/AcroForm" in pdf_dst.Root:
            del pdf_dst.Root["/AcroForm"]
        return

    for parent_key, children in children_by_parent.items():
        parent = objects_by_key[parent_key]
        parent["/Kids"] = pikepdf.Array(children)

    for root_field in root_fields:
        if "/Parent" in root_field:
            del root_field["/Parent"]

    fields_by_original_name = {}
    for root_field in root_fields:
        name = _field_name(root_field)
        if name:
            fields_by_original_name.setdefault(name, []).append(root_field)

    _rename_duplicate_root_fields(root_fields)

    acroform = _copy_safe_acroform_globals(pdf_src, pdf_dst)
    acroform["/Fields"] = pikepdf.Array(root_fields)
    calculation_order = _filtered_calculation_order(pdf_src, fields_by_original_name)
    if len(calculation_order) > 0:
        acroform["/CO"] = calculation_order
    pdf_dst.Root["/AcroForm"] = pdf_dst.make_indirect(acroform)


def _append_source_page(
    src_path: str,
    pdf_src: pikepdf.Pdf,
    pdf_dst: pikepdf.Pdf,
    page_number: int,
    occurrence: int,
) -> pikepdf.Page:
    if occurrence == 1:
        pdf_dst.pages.append(pdf_src.pages[page_number - 1])
    else:
        with pikepdf.open(src_path) as fresh_src:
            pdf_dst.pages.append(fresh_src.pages[page_number - 1])
    return pdf_dst.pages[-1]


def _rotate_pikepdf_page(page: pikepdf.Page, extra: int) -> None:
    try:
        base = int(page.get("/Rotate", 0)) % 360
    except Exception:
        base = 0
    new = (base + (extra or 0)) % 360
    if new == 0:
        try:
            del page["/Rotate"]
        except Exception:
            pass
    else:
        page.Rotate = new


def pdf_requires_content_preservation(path: str) -> dict:
    """
    Return non-sensitive booleans describing whether a PDF must avoid
    destructive qpdf/Ghostscript processing.
    """
    result = {
        "requires_preservation": False,
        "has_acroform": False,
        "has_filled_fields": False,
        "has_widgets": False,
        "has_signature_fields": False,
        "has_annotations": False,
        "has_annotation_appearances": False,
        "has_need_appearances": False,
        "has_sigflags": False,
    }

    with pikepdf.open(path, suppress_warnings=True) as pdf:
        root = pdf.Root
        acroform = root.get("/AcroForm")
        fields = list(acroform.get("/Fields", pikepdf.Array())) if acroform else []
        result["has_acroform"] = bool(fields)
        result["has_need_appearances"] = bool(acroform and acroform.get("/NeedAppearances"))
        result["has_sigflags"] = bool(acroform and "/SigFlags" in acroform)

        for field in fields:
            for node in _walk_fields(field):
                if "/V" in node:
                    result["has_filled_fields"] = True
                if _field_type(node) == "/Sig":
                    result["has_signature_fields"] = True
                if str(node.get("/Subtype", "")) == "/Widget":
                    result["has_widgets"] = True
                if node.get("/AP") is not None:
                    result["has_annotation_appearances"] = True

        for page in pdf.pages:
            annots = list(_iter_page_annots(page))
            if annots:
                result["has_annotations"] = True
            for annot in annots:
                if str(annot.get("/Subtype", "")) == "/Widget":
                    result["has_widgets"] = True
                if annot.get("/AP") is not None:
                    result["has_annotation_appearances"] = True
                if "/V" in annot:
                    result["has_filled_fields"] = True
                field_type = _field_type(annot)
                parent = annot.get("/Parent") if hasattr(annot, "get") else None
                if not field_type and hasattr(parent, "get"):
                    field_type = _field_type(parent)
                if field_type == "/Sig":
                    result["has_signature_fields"] = True

    result["requires_preservation"] = any(
        result[key]
        for key in (
            "has_acroform",
            "has_widgets",
            "has_signature_fields",
            "has_annotations",
            "has_annotation_appearances",
            "has_need_appearances",
            "has_sigflags",
        )
    )
    return result


def pdf_preservation_warnings(requirements: dict, resize_to_a4: bool = False) -> list[str]:
    warnings = [PDF_PRESERVATION_WARNING]
    if resize_to_a4:
        warnings.append(PDF_RESIZE_IGNORED_WARNING)
    if requirements.get("has_signature_fields"):
        warnings.append(PDF_SIGNATURE_REWRITE_WARNING)
    return warnings


def write_preserving_pdf_subset(
    input_path: str,
    output_path: str,
    pages: Optional[List[int]] = None,
    rotations: Optional[Dict[int, int]] = None,
    page_transform: Optional[Callable[[pikepdf.Page, int], None]] = None,
) -> dict:
    """
    Losslessly writes selected pages while rebuilding page-scoped AcroForm data.

    The helper preserves page order, duplicate page selections, widgets,
    annotations, /AP appearances, /Parent, /Kids, /P and a filtered /CO.
    It also strips dangerous catalog/form action keys inherited from the source.
    """
    rot_map: Dict[int, int] = {}
    for key, value in (rotations or {}).items():
        try:
            pn = int(key)
            deg = int(value) % 360
        except Exception:
            continue
        if deg not in (0, 90, 180, 270):
            deg = (round(deg / 90) * 90) % 360
        if deg:
            rot_map[pn] = deg

    with pikepdf.open(input_path, suppress_warnings=True) as pdf_src, pikepdf.Pdf.new() as pdf_dst:
        total = len(pdf_src.pages)
        if pages is None:
            pages_to_emit = list(range(1, total + 1))
        else:
            pages_to_emit = []
            for value in pages:
                try:
                    page_number = int(value)
                except (TypeError, ValueError):
                    continue
                if 1 <= page_number <= total:
                    pages_to_emit.append(page_number)
        if not pages_to_emit:
            raise ValueError("Nenhuma pagina valida para preservar.")

        seen_pages: Dict[int, int] = {}
        for page_number in pages_to_emit:
            seen_pages[page_number] = seen_pages.get(page_number, 0) + 1
            dst_page = _append_source_page(
                input_path,
                pdf_src,
                pdf_dst,
                page_number,
                seen_pages[page_number],
            )
            if page_number in rot_map:
                _rotate_pikepdf_page(dst_page, rot_map[page_number])
            if page_transform is not None:
                page_transform(dst_page, page_number)

        _rebuild_acroform_for_output(pdf_src, pdf_dst)
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        pdf_dst.save(output_path)

    return {
        "pages_in": total,
        "pages_out": len(pages_to_emit),
        "output_path": output_path,
    }

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
