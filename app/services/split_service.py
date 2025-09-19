# app/services/split_service.py
import os
import uuid
from typing import Dict, List, Optional

from flask import current_app
import pikepdf
from PyPDF2 import PdfReader  # apenas para contagem simples
from werkzeug.exceptions import BadRequest

from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.limits import enforce_pdf_page_limit, enforce_total_pages
from .sanitize_service import sanitize_pdf  # remove JS/anotações


def _page_count(path: str) -> int:
    with open(path, "rb") as f:
        return len(PdfReader(f).pages)


def _rotate_page(page: pikepdf.Page, extra: int):
    """Aplica rotação extra (0/90/180/270) preservando a rotação base."""
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


def _apply_crop(page: pikepdf.Page, crop_norm: Dict[str, float]):
    """
    Converte crop normalizado (x,y,w,h) em 0..1 (origem: topo-esquerda)
    para PDF user space (origem: canto inferior-esquerda) e aplica em CropBox/MediaBox.
    """
    mb = page.MediaBox
    x0 = float(mb[0]); y0 = float(mb[1]); x1 = float(mb[2]); y1 = float(mb[3])
    W = x1 - x0; H = y1 - y0

    x = max(0.0, min(1.0, float(crop_norm["x"])))
    y = max(0.0, min(1.0, float(crop_norm["y"])))
    w = max(0.0, min(1.0, float(crop_norm["w"])))
    h = max(0.0, min(1.0, float(crop_norm["h"])))

    left   = x0 + x * W
    right  = x0 + (x + w) * W
    top    = y1 - y * H
    bottom = y1 - (y + h) * H

    left, right = min(left, right), max(left, right)
    bottom, top = min(bottom, top), max(bottom, top)

    page.CropBox  = pikepdf.Array([left, bottom, right, top])
    page.MediaBox = pikepdf.Array([left, bottom, right, top])


def dividir_pdf(file, pages: Optional[List[int]] = None,
                rotations: Optional[Dict[int, int]] = None,
                modificacoes: Optional[Dict[int, Dict]] = None) -> List[str]:
    """
    Divide/seleciona páginas de um PDF.
    - Se 'pages' vier: retorna [<PDF único com as selecionadas na ordem dada>]
    - Se 'pages' não vier: retorna [<PDF pág 1>, <PDF pág 2>, ...]
    Retorna caminhos absolutos no UPLOAD_FOLDER.
    """
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)

    # 1) Validar e salvar o upload com nome sanitizado + checagem de MIME real
    filename = validate_upload(file, {"pdf"})
    in_name = f"{uuid.uuid4().hex}_{filename}"
    in_path = os.path.join(upload_folder, in_name)
    file.save(in_path)

    try:
        # 2) Limites de segurança
        enforce_pdf_page_limit(in_path, label=filename)

        # 3) Sanitização do PDF (remove JS/anotações) → fonte segura
        safe_path = os.path.join(upload_folder, f"safe_{uuid.uuid4().hex}.pdf")
        try:
            sanitize_pdf(in_path, safe_path)
            src = safe_path
        except Exception:
            current_app.logger.warning("Falha ao sanitizar PDF — usando arquivo original.")
            src = in_path

        # 4) Contagem de páginas e normalização de parâmetros
        total = _page_count(src)

        if pages:
            pages_to_emit = [int(p) for p in pages if 1 <= int(p) <= total]
            if not pages_to_emit:
                raise BadRequest("Nenhuma página válida foi selecionada.")
        else:
            pages_to_emit = list(range(1, total + 1))

        enforce_total_pages(len(pages_to_emit))

        rot_map: Dict[int, int] = {}
        if isinstance(rotations, dict):
            for k, v in rotations.items():
                try:
                    kk = int(k)
                    vv = int(v) % 360
                    if vv not in (0, 90, 180, 270):
                        vv = (round(vv / 90) * 90) % 360
                    if vv != 0:
                        rot_map[kk] = vv
                except Exception:
                    continue

        mods_map: Dict[int, Dict] = {}
        if isinstance(modificacoes, dict):
            for k, v in modificacoes.items():
                try:
                    kk = int(k)
                    if isinstance(v, dict):
                        mods_map[kk] = v
                except Exception:
                    continue

        outputs: List[str] = []

        # 5A) Caso "selecionadas" → único PDF
        if pages:
            out_path = os.path.join(upload_folder, f"selecionadas_{uuid.uuid4().hex}.pdf")
            with pikepdf.open(src) as pdf_src, pikepdf.Pdf.new() as pdf_dst:
                for p1 in pages_to_emit:
                    page = pdf_src.pages[p1 - 1]
                    pdf_dst.pages.append(page)
                    dst_page = pdf_dst.pages[-1]

                    if p1 in rot_map:
                        _rotate_page(dst_page, rot_map[p1])

                    m = mods_map.get(p1)
                    if m and isinstance(m, dict):
                        crop = m.get("crop")
                        if crop:
                            _apply_crop(dst_page, crop)

                pdf_dst.save(out_path)
            outputs.append(out_path)
            return outputs

        # 5B) Caso "split total" → um PDF por página
        with pikepdf.open(src) as pdf_src:
            for p1 in pages_to_emit:
                page = pdf_src.pages[p1 - 1]
                out_path = os.path.join(upload_folder, f"pagina_{p1}_{uuid.uuid4().hex}.pdf")
                with pikepdf.Pdf.new() as outp:
                    outp.pages.append(page)
                    dst_page = outp.pages[-1]

                    if p1 in rot_map:
                        _rotate_page(dst_page, rot_map[p1])

                    m = mods_map.get(p1)
                    if m and isinstance(m, dict):
                        crop = m.get("crop")
                        if crop:
                            _apply_crop(dst_page, crop)

                    outp.save(out_path)
                outputs.append(out_path)

        return outputs

    finally:
        # limpeza best-effort
        try:
            os.remove(in_path)
        except OSError:
            pass
        try:
            if os.path.exists(safe_path):
                os.remove(safe_path)
        except Exception:
            pass