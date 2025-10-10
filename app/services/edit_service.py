# app/services/edit_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, io, tempfile, logging
from typing import Dict, Any, List, Tuple

import fitz  # PyMuPDF
import pikepdf

from .ocr_service import ocr_pdf_path  # ⟵ usa o wrapper com fallback/sanitização

logger = logging.getLogger(__name__)

def _tmp_pdf_path(suffix=".pdf") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix); os.close(fd); return path

def _sanitize_pdf(in_path: str) -> str:
    """
    Remove JavaScript, OpenAction/AA e metadados. Lineariza.
    """
    out_path = _tmp_pdf_path()
    with pikepdf.open(in_path) as pdf:
        # Remover JavaScript embutido
        try:
            if "/Names" in pdf.root and "/JavaScript" in pdf.root.Names:
                del pdf.root.Names["/JavaScript"]
        except Exception:
            pass
        # OpenAction / Additional Actions
        for key in ("/OpenAction", "/AA"):
            try:
                if key in pdf.root:
                    del pdf.root[key]
            except Exception:
                pass
        # Limpar metadados
        try:
            pdf.docinfo.clear()
        except Exception:
            pass
        pdf.save(out_path, linearize=True)
    return out_path

def _apply_ocr_if_needed(in_path: str, timeout: int = 300) -> str:
    """
    Aplica OCR apenas em páginas sem texto (via --skip-text).
    Reutiliza o serviço de OCR com fallback automático de optimize (pngquant).
    """
    out_path = _tmp_pdf_path()
    try:
        ocr_pdf_path(
            in_path, out_path,
            skip_text=True, force=False,
            optimize=2,            # prefere 2; cai para 1 se faltar pngquant
            deskew=True, rotate_pages=True,
            clean=True,            # entra somente se unpaper existir
            jobs=1, timeout=timeout
        )
        return out_path
    except Exception as e:
        logger.warning("OCR falhou/ignorado: %s", e)
        # se falhar, retorna o próprio arquivo de entrada (sem quebrar o fluxo de edição)
        try:
            os.remove(out_path)
        except Exception:
            pass
        return in_path

def _norm_to_abs_rect(nbox: Dict[str, float], page_rect: fitz.Rect) -> fitz.Rect:
    """
    Converte (x,y,w,h) normalizados (origem top-left, 0..1) para retângulo absoluto em pontos PDF.
    """
    x0 = page_rect.x0 + float(nbox["x"]) * page_rect.width
    y0 = page_rect.y0 + float(nbox["y"]) * page_rect.height
    x1 = x0 + float(nbox["w"]) * page_rect.width
    y1 = y0 + float(nbox["h"]) * page_rect.height
    return fitz.Rect(x0, y0, x1, y1)

def apply_edits(in_path: str, ops: Dict[str, Any], ocr: bool = False) -> str:
    """
    ops schema:
    {
      "reorder": [0,2,1,3],
      "delete": [5,6],
      "rotate": {"0": 90, "2": 270},
      "crop":   {"1": {"x":0.1,"y":0.2,"w":0.5,"h":0.6}},
      "redact": {"2": [ {"x":0.05,"y":0.1,"w":0.3,"h":0.08}, ... ]},
      "text":   {"3": [ {"x":0.2,"y":0.15,"text":"CONFIDENCIAL","size":18}, ... ]}
    }
    Índices referem-se à ordem original; por isso aplicamos antes do reorder/drop.
    """
    ops = ops or {}
    reorder = ops.get("reorder")
    delete = set(map(int, ops.get("delete", [])))
    rotate = {int(k): int(v) for k, v in (ops.get("rotate") or {}).items()}
    crop   = {int(k): v for k, v in (ops.get("crop") or {}).items()}
    redact = {int(k): v for k, v in (ops.get("redact") or {}).items()}
    text   = {int(k): v for k, v in (ops.get("text") or {}).items()}

    doc = fitz.open(in_path)
    total = doc.page_count

    # Valida reorder
    base_idxs = list(range(total))
    if reorder:
        if len(reorder) != total or sorted(reorder) != base_idxs:
            raise ValueError("Lista de reorder inválida.")
        reorder = list(map(int, reorder))
    else:
        reorder = base_idxs

    # 1) aplica edições por índice original
    for i in range(total):
        page = doc.load_page(i)
        rect = page.rect  # origem top-left

        # crop primeiro (combina melhor com a UI)
        if i in crop:
            r = _norm_to_abs_rect(crop[i], rect)
            page.set_cropbox(r)
            rect = r  # atualiza base para outras marcações

        # retângulos opacos (redação visual)
        if i in redact:
            for nb in redact[i]:
                r = _norm_to_abs_rect(nb, rect)
                page.draw_rect(r, fill=(0, 0, 0), color=None, width=0)

        # texto
        if i in text:
            for tb in text[i]:
                x = rect.x0 + float(tb["x"]) * rect.width
                y = rect.y0 + float(tb["y"]) * rect.height
                size = int(tb.get("size", 14))
                s = str(tb.get("text", ""))
                # fonte padrão Helvetica (built-in)
                page.insert_text((x, y), s, fontsize=size, fontname="helv", color=(0, 0, 0))

        # rotação por último
        if i in rotate:
            ang = rotate[i] % 360
            page.set_rotation(ang)

    # 2) constrói ordem final (reorder - delete)
    keep_order = [i for i in reorder if i not in delete]

    # reordena/filtra in-place
    doc.select(keep_order)

    # 3) salva, sanitiza e (opcional) OCR
    tmp_out = _tmp_pdf_path()
    doc.save(tmp_out, deflate=True, garbage=3)
    doc.close()

    sanitized = _sanitize_pdf(tmp_out)
    final_path = _apply_ocr_if_needed(sanitized) if ocr else sanitized

    try: os.remove(tmp_out)
    except Exception: pass
    if final_path != sanitized:
        try: os.remove(sanitized)
        except Exception: pass

    return final_path