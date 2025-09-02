# app/utils/mime.py
import io
try:
    import magic
except Exception:
    magic = None

PDF_SIG = b"%PDF-"

def detect_mime_or_ext(upload_or_path, default="application/octet-stream"):
    """
    Detecta MIME real. Usa python-magic se disponível; fallback: assinatura PDF.
    Aceita arquivo de upload (obj com .stream) ou caminho str.
    """
    if magic:
        try:
            if hasattr(upload_or_path, "stream"):
                pos = upload_or_path.stream.tell()
                head = upload_or_path.stream.read(8192)
                upload_or_path.stream.seek(pos)
                return magic.from_buffer(head, mime=True)
            else:
                return magic.from_file(str(upload_or_path), mime=True)
        except Exception:
            pass
    # fallback básico
    try:
        if hasattr(upload_or_path, "stream"):
            pos = upload_or_path.stream.tell()
            head = upload_or_path.stream.read(5)
            upload_or_path.stream.seek(pos)
            if head == PDF_SIG: return "application/pdf"
        else:
            with open(upload_or_path, "rb") as fh:
                if fh.read(5) == PDF_SIG: return "application/pdf"
    except Exception:
        pass
    return default