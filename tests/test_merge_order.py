from io import BytesIO
from werkzeug.datastructures import FileStorage
from PyPDF2 import PdfWriter, PdfReader
from app import create_app
from app.services import merge_service


def _pdf(w, h):
    writer = PdfWriter()
    writer.add_blank_page(width=w, height=h)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_juntar_pdfs_respeita_ordem(tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    with app.app_context():
        f1 = FileStorage(stream=_pdf(10, 20), filename="a.pdf")
        f2 = FileStorage(stream=_pdf(30, 40), filename="b.pdf")
        output = merge_service.juntar_pdfs([f2, f1])
        reader = PdfReader(output)
        w0 = float(reader.pages[0].mediabox.width)
        h0 = float(reader.pages[0].mediabox.height)
        w1 = float(reader.pages[1].mediabox.width)
        h1 = float(reader.pages[1].mediabox.height)
        assert (w0, h0) == (30, 40)
        assert (w1, h1) == (10, 20)
