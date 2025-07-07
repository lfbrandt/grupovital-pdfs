import os
from io import BytesIO
from PIL import Image
from werkzeug.datastructures import FileStorage
from PyPDF2 import PdfWriter, PdfReader
import pytest

from app import create_app
from app.services import converter_service, split_service


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    return app


def _simple_pdf(size=(10, 10)):
    writer = PdfWriter()
    writer.add_blank_page(width=size[0], height=size[1])
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_converter_rotation(app):
    with app.app_context():
        img = Image.new("RGB", (10, 20), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        file = FileStorage(stream=buf, filename="img.png")
        out = converter_service.converter_doc_para_pdf(file, modificacoes={"rotate": 90})
        reader = PdfReader(out)
        w = float(reader.pages[0].mediabox.width)
        h = float(reader.pages[0].mediabox.height)
        assert (w, h) == (20, 10)


def test_split_crop(app):
    with app.app_context():
        buf = _simple_pdf(size=(10, 10))
        file = FileStorage(stream=buf, filename="a.pdf")
        outputs = split_service.dividir_pdf(file, modificacoes={"crop": [0, 0, 5, 5]})
        reader = PdfReader(outputs[0])
        w = float(reader.pages[0].mediabox.width)
        h = float(reader.pages[0].mediabox.height)
        assert (w, h) == (5, 5)
        for p in outputs:
            os.remove(p)
