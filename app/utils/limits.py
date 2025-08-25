import os
from werkzeug.exceptions import BadRequest
from PyPDF2 import PdfReader

def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)))
    except Exception:
        return default

# Limites configuráveis por .env (valores padrão seguros)
_MAX_PDF_PAGES_DEFAULT = 800
_MAX_TOTAL_PAGES_DEFAULT = 2000

def get_max_pdf_pages() -> int:
    return _int_env("MAX_PDF_PAGES", _MAX_PDF_PAGES_DEFAULT)

def get_max_total_pages() -> int:
    return _int_env("MAX_TOTAL_PAGES", _MAX_TOTAL_PAGES_DEFAULT)

def count_pages(path: str) -> int:
    with open(path, "rb") as f:
        return len(PdfReader(f).pages)

def enforce_pdf_page_limit(path: str, *, label: str = "arquivo", max_pages: int | None = None) -> int:
    """
    Garante que o PDF em 'path' não excede o limite por arquivo.
    Retorna a contagem de páginas se estiver OK; lança BadRequest se exceder.
    """
    limit = max_pages if isinstance(max_pages, int) else get_max_pdf_pages()
    pages = count_pages(path)
    if pages > limit:
        raise BadRequest(
            f"O PDF '{os.path.basename(path)}' possui {pages} páginas, acima do limite de {limit}. "
            "Reduza o documento ou aumente 'MAX_PDF_PAGES' nas variáveis de ambiente."
        )
    return pages

def enforce_total_pages(total_pages: int, *, max_total: int | None = None) -> None:
    """
    Garante que a soma total de páginas de uma operação (ex.: merge/split) não excede o limite global.
    Lança BadRequest se exceder.
    """
    limit = max_total if isinstance(max_total, int) else get_max_total_pages()
    if total_pages > limit:
        raise BadRequest(
            f"A seleção tem {total_pages} páginas, acima do limite global de {limit}. "
            "Ajuste a seleção ou aumente 'MAX_TOTAL_PAGES' na configuração."
        )