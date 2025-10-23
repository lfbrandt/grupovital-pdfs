# -*- coding: utf-8 -*-
"""
Limites e validações padronizadas do projeto.

Compat preservada com o que você já usava:
- get_max_pdf_pages()
- get_max_total_pages()
- count_pages(path)
- enforce_pdf_page_limit(path, label="arquivo", max_pages=None) -> int
- enforce_total_pages(total_pages, max_total=None) -> None

Novidades seguras:
- LIMIT_MAX_MERGE_FILES (+ get_max_merge_files)
- LIMIT_MAX_RUNTIME_PER_JOB (+ helpers de deadline job_deadline_start/job_deadline_check)
- Timeouts GS/LO/OCR com aliases de env (GS_TIMEOUT/GHOSTSCRIPT_TIMEOUT, LO_TIMEOUT/LO_CONVERT_TIMEOUT_SEC)
- enforce_total_files(n, label="arquivos")
"""

from __future__ import annotations

import os
import time
from typing import Optional

from werkzeug.exceptions import BadRequest

try:
    from PyPDF2 import PdfReader  # contagem rápida de páginas
except Exception:
    PdfReader = None  # type: ignore[assignment]


# =========================
# Helpers de leitura de env
# =========================
def _int_env(names, default: int) -> int:
    """
    Lê o primeiro env int válido dentre 'names' (str ou lista/tupla). Fallback em 'default'.
    """
    if isinstance(names, (list, tuple)):
        candidates = list(names)
    else:
        candidates = [str(names)]
    for name in candidates:
        raw = os.environ.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(str(raw).strip())
        except Exception:
            # ignora inválido e tenta o próximo alias
            continue
    return int(default)


# =========================
# Defaults seguros do projeto
# =========================
_MAX_PDF_PAGES_DEFAULT = 800          # limite por arquivo PDF
_MAX_TOTAL_PAGES_DEFAULT = 2000       # soma de páginas em uma operação (merge/split)
_MAX_MERGE_FILES_DEFAULT = 25         # quantidade máx. de arquivos no merge
_MAX_RUNTIME_JOB_DEFAULT = 180        # segundos (fail-fast em jobs longos)


# =========================
# Leitura de ENV com aliases
# =========================
def get_max_pdf_pages() -> int:
    # Mantém seu nome existente e aceita alias "LIMIT_MAX_PAGES".
    return _int_env(["MAX_PDF_PAGES", "LIMIT_MAX_PAGES"], _MAX_PDF_PAGES_DEFAULT)

def get_max_total_pages() -> int:
    # Aceita alias "LIMIT_MAX_TOTAL_PAGES" se você quiser padronizar futuramente.
    return _int_env(["MAX_TOTAL_PAGES", "LIMIT_MAX_TOTAL_PAGES"], _MAX_TOTAL_PAGES_DEFAULT)

def get_max_merge_files() -> int:
    return _int_env(["LIMIT_MAX_MERGE_FILES", "MAX_MERGE_FILES"], _MAX_MERGE_FILES_DEFAULT)

def get_max_runtime_per_job() -> int:
    return _int_env(["LIMIT_MAX_RUNTIME_PER_JOB", "MAX_RUNTIME_PER_JOB"], _MAX_RUNTIME_JOB_DEFAULT)


# Exposição opcional como “constantes” (fixadas no import do módulo)
LIMIT_MAX_MERGE_FILES = get_max_merge_files()
LIMIT_MAX_RUNTIME_PER_JOB = get_max_runtime_per_job()

# Timeouts de processos externos (aliases suportados)
GS_TIMEOUT = _int_env(["GS_TIMEOUT", "GHOSTSCRIPT_TIMEOUT"], 60)
LO_TIMEOUT = _int_env(["LO_TIMEOUT", "LO_CONVERT_TIMEOUT_SEC", "LIBREOFFICE_TIMEOUT"], 120)
OCR_TIMEOUT = _int_env(["OCR_TIMEOUT", "TESSERACT_TIMEOUT"], 120)


# =========================
# Utilidades de páginas/arquivos
# =========================
def count_pages(path: str) -> int:
    """
    Retorna a contagem de páginas de um PDF.
    Lança BadRequest se houver falha na leitura.
    """
    if PdfReader is None:
        raise BadRequest("Dependência ausente: PyPDF2 não disponível para leitura de páginas.")
    try:
        with open(path, "rb") as f:
            return len(PdfReader(f).pages)
    except Exception:
        raise BadRequest(f"O arquivo '{os.path.basename(path)}' é inválido ou está corrompido.")


def enforce_pdf_page_limit(path: str, *, label: str = "arquivo", max_pages: int | None = None) -> int:
    """
    Garante que o PDF em 'path' não excede o limite por arquivo.
    Retorna a contagem de páginas se estiver OK; lança BadRequest se exceder.
    """
    limit = int(max_pages) if isinstance(max_pages, int) else get_max_pdf_pages()
    pages = count_pages(path)
    if pages <= 0:
        raise BadRequest(f"O {label} '{os.path.basename(path)}' não possui páginas válidas.")
    if pages > limit:
        raise BadRequest(
            f"O {label} '{os.path.basename(path)}' possui {pages} páginas, acima do limite de {limit}. "
            "Reduza o documento ou ajuste 'MAX_PDF_PAGES' nas variáveis de ambiente."
        )
    return pages


def enforce_total_pages(total_pages: int, *, max_total: int | None = None) -> None:
    """
    Garante que a soma total de páginas de uma operação (ex.: merge/split) não excede o limite global.
    Lança BadRequest se exceder.
    """
    try:
        n = int(total_pages)
    except Exception:
        raise BadRequest("Total de páginas inválido.")
    limit = int(max_total) if isinstance(max_total, int) else get_max_total_pages()
    if n <= 0:
        raise BadRequest("Seleção não possui páginas válidas.")
    if n > limit:
        raise BadRequest(
            f"A seleção tem {n} páginas, acima do limite global de {limit}. "
            "Ajuste a seleção ou aumente 'MAX_TOTAL_PAGES' na configuração."
        )


def enforce_total_files(n_files: int, *, label: str = "arquivos", max_files: int | None = None) -> None:
    """
    Garante que a quantidade de arquivos não excede o limite configurado (ex.: /api/merge).
    Lança BadRequest se exceder.
    """
    try:
        n = int(n_files)
    except Exception:
        raise BadRequest("Quantidade de arquivos inválida.")
    limit = int(max_files) if isinstance(max_files, int) else get_max_merge_files()
    if n < 1:
        raise BadRequest("Nenhum arquivo enviado.")
    if n > limit:
        raise BadRequest(f"Muitos {label} ({n}). Limite: {limit}.")


# =========================
# Deadline simples para jobs
# =========================
def job_deadline_start(seconds: Optional[int] = None) -> float:
    """
    Marca um deadline monotônico para o job atual.
    Use junto com job_deadline_check(deadline).
    """
    secs = int(seconds) if isinstance(seconds, int) and seconds > 0 else get_max_runtime_per_job()
    return time.monotonic() + max(1, secs)


def job_deadline_check(deadline_monotonic: float, *, label: str = "Job") -> None:
    """
    Levanta BadRequest se o deadline foi estourado (a rota pode mapear para 408/504).
    """
    try:
        if time.monotonic() > float(deadline_monotonic):
            raise BadRequest(f"{label} excedeu o tempo máximo permitido.")
    except BadRequest:
        raise
    except Exception:
        raise BadRequest(f"Prazo máximo do {label} expirado.")