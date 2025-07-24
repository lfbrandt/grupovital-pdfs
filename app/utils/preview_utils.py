import base64
from io import BytesIO
from typing import List
from PyPDF2 import PdfReader
from PIL import Image


def preview_pdf(file) -> List[str]:
    """Return base64 thumbnails for each page of the given PDF.

    This is a lightweight placeholder implementation that generates a
    blank image for each page. It avoids heavy dependencies like
    pdf2image which require external binaries.
    """
    reader = PdfReader(file)
    thumb = _blank_thumbnail()
    return [thumb for _ in reader.pages]


def _blank_thumbnail() -> str:
    img = Image.new("RGB", (120, 160), color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"
