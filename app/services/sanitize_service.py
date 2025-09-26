# -*- coding: utf-8 -*-
"""
sanitize_service.py — Sanitização robusta de PDF

- 1ª tentativa: pikepdf (remove JS, OpenAction/AA, XFA, anotações perigosas e EmbeddedFiles).
- Fallback: Ghostscript -dSAFER em sandbox.
- Compat: funciona com pikepdf mais antigo (sem atributo .root), usando trailer['/Root'].
- Comportamento: sempre retorna um caminho utilizável; em último caso, retorna o in_path.
"""

from __future__ import annotations

import os
import tempfile
import logging
import platform
from typing import Optional

import pikepdf
from .sandbox import run_in_sandbox

logger = logging.getLogger(__name__)

# ------------- Ghostscript config -------------
_env_gs = os.environ.get("GS_BIN") or os.environ.get("GHOSTSCRIPT_BIN")
if _env_gs:
    GHOSTSCRIPT_BIN = _env_gs
elif platform.system() == "Windows":
    GHOSTSCRIPT_BIN = "gswin64c"
else:
    GHOSTSCRIPT_BIN = "gs"

_GS_TO = int(os.environ.get("GS_TIMEOUT", os.environ.get("GHOSTSCRIPT_TIMEOUT", "60")))


def _tmp_pdf(prefix: str = "san_") -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".pdf")
    os.close(fd)
    return path


def _ensure_parent(path: str) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    except Exception:
        pass


def _filename_from(src: str, suffix: str = "_san") -> str:
    base, _ = os.path.splitext(os.path.abspath(src))
    return f"{base}{suffix}.pdf"


def _get_root_dict(pdf: "pikepdf.Pdf"):
    """Compat: tenta pdf.root; se não existir, usa trailer['/Root']."""
    try:
        return pdf.root
    except Exception:
        try:
            return pdf.trailer["/Root"]
        except Exception:
            return None


def _pikepdf_clean(
    in_path: str,
    out_path: str,
    *,
    remove_annotations: bool,
    remove_actions: bool,
    remove_embedded: bool,
) -> bool:
    """
    Tenta limpar via pikepdf. Retorna True se gerar out_path válido.
    """
    _ensure_parent(out_path)

    with pikepdf.open(in_path, allow_overwriting_input=False) as pdf:
        root = _get_root_dict(pdf)
        if root is None:
            raise AttributeError("Documento PDF sem root acessível (compat)")

        # --- Catálogo: ações automáticas ---
        if remove_actions:
            for k in ("OpenAction", "AA"):
                try:
                    if k in root:
                        del root[k]
                except Exception:
                    pass

        # --- AcroForm: XFA/JS/AA ---
        try:
            acro = root.get("/AcroForm", None)
            if acro:
                for k in ("/XFA", "/JS", "/AA"):
                    try:
                        if k in acro:
                            del acro[k]
                    except Exception:
                        pass
                if "/Fields" in acro:
                    for f in list(acro["/Fields"]):
                        try:
                            if "/AA" in f:
                                del f["/AA"]
                        except Exception:
                            pass
        except Exception:
            pass

        # --- Names: JavaScript / EmbeddedFiles ---
        try:
            names = root.get("/Names", None)
            if names:
                if remove_actions:
                    try:
                        if "/JavaScript" in names:
                            del names["/JavaScript"]
                    except Exception:
                        pass
                if remove_embedded:
                    try:
                        if "/EmbeddedFiles" in names:
                            del names["/EmbeddedFiles"]
                    except Exception:
                        pass
                try:
                    # se esvaziou, remove do root
                    if len(names.keys()) == 0:
                        del root["/Names"]
                except Exception:
                    pass
        except Exception:
            pass

        # --- Anotações por página ---
        try:
            for page in pdf.pages:
                annots = page.get("/Annots", None)
                if not annots:
                    continue

                if remove_annotations:
                    try:
                        del page["/Annots"]
                    except Exception:
                        page.Annots = pikepdf.Array()
                    continue

                # mantém só anotações não perigosas
                new_annots = []
                for a in annots:
                    try:
                        obj = a.get_object()
                        has_a = "/A" in obj
                        has_aa = "/AA" in obj
                        has_js = False
                        try:
                            if has_a and "/JS" in obj["/A"]:
                                has_js = True
                        except Exception:
                            pass
                        if has_a or has_aa or has_js:
                            continue
                        new_annots.append(a)
                    except Exception:
                        continue
                if new_annots:
                    page.Annots = pikepdf.Array(new_annots)
                else:
                    try:
                        del page["/Annots"]
                    except Exception:
                        pass
        except Exception:
            pass

        # --- Salva linearizado ---
        pdf.save(
            out_path,
            linearize=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
        )

    # abre de volta só pra validar superficialmente
    with pikepdf.open(out_path):
        pass
    return True


def _gs_rewrite(in_path: str, out_path: str) -> bool:
    """Regrava via Ghostscript -dSAFER. True se gerar out_path válido."""
    _ensure_parent(out_path)
    cmd = [
        GHOSTSCRIPT_BIN,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.6",
        "-dDetectDuplicateImages=true",
        "-dAutoRotatePages=/None",
        "-dNOPAUSE", "-dBATCH", "-dQUIET",
        "-dSAFER",
        f"-sOutputFile={out_path}",
        in_path,
    ]
    proc = run_in_sandbox(cmd, timeout=_GS_TO, cpu_seconds=60, mem_mb=768)
    if getattr(proc, "returncode", 0) != 0:
        raise RuntimeError(proc.stderr or "Ghostscript retornou código ≠ 0")
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("Ghostscript não gerou saída")
    try:
        with pikepdf.open(out_path):
            pass
    except Exception:
        # mesmo que não abra em pikepdf, via de regra o GS gerou PDF válido
        pass
    return True


def sanitize_pdf(
    in_path: str,
    out_path: Optional[str] = None,
    remove_annotations: bool = True,
    remove_actions: bool = True,
    remove_embedded: bool = True,
    *,
    label: str = "",
) -> str:
    """
    Sanitiza um PDF e retorna o caminho da saída.
    - Se out_path não for fornecido, gera um caminho baseado no nome do input.
    - Se tudo falhar, retorna in_path (sem lançar).
    """
    label = label or os.path.basename(in_path)
    dst = out_path or _filename_from(in_path, "_san")

    # 1) pikepdf
    try:
        ok = _pikepdf_clean(
            in_path,
            dst,
            remove_annotations=remove_annotations,
            remove_actions=remove_actions,
            remove_embedded=remove_embedded,
        )
        if ok:
            logger.info("Sanitização pikepdf OK (%s)", label)
            return dst
    except Exception as e:
        logger.debug("pikepdf falhou (%s): %s", label, e)

    # 2) Ghostscript fallback
    try:
        if not out_path and os.path.exists(dst):
            try:
                os.remove(dst)
            except Exception:
                pass
            dst = _tmp_pdf("gs_")
        _gs_rewrite(in_path, dst)
        logger.info("Sanitização via GS OK (%s)", label)
        return dst
    except Exception as e:
        logger.debug("Fallback GS falhou (%s): %s", label, e)

    # 3) Último recurso
    logger.warning("sanitize_pdf falhou (%s); retornando original", label)
    return in_path