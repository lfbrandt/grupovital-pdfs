from io import BytesIO
from PyPDF2 import PdfWriter, PdfReader
from app.services.postprocess_service import aplicar_modificacoes


def _simple_pdf(width=100, height=100):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_aplicar_modificacoes_rotacao_e_crop(tmp_path):
    pdf_path = tmp_path / "in.pdf"
    with open(pdf_path, "wb") as f:
        f.write(_simple_pdf(100, 100).read())

    mods = [{"rotacao": 90, "crop": {"t": 10, "r": 10, "b": 10, "l": 10}}]
    out = aplicar_modificacoes(str(pdf_path), mods)

    reader = PdfReader(out)
    page = reader.pages[0]
    assert page.get("/Rotate") == 90
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    assert (width, height) == (80, 80)
