import os
from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage
from PyPDF2 import PdfWriter

from app import create_app
from app.services import (
    compress_service,
    converter_service,
    merge_service,
    split_service,
)


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    return app


def _simple_pdf(page_count=1):
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_comprimir_pdf_honors_config(monkeypatch, app, tmp_path):
    def fake_run(cmd, check=True, timeout=60):
        for part in cmd:
            if str(part).startswith("-sOutputFile="):
                path = part.split("=", 1)[1]
                open(path, "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(), filename="a.pdf")
        output = compress_service.comprimir_pdf(file)
        assert str(tmp_path) in output
        assert os.path.exists(output)


def test_converter_doc_para_pdf_honors_config(app, tmp_path):
    from PIL import Image

    with app.app_context():
        img = Image.new("RGB", (1, 1), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        file = FileStorage(stream=buf, filename="img.png")
        output = converter_service.converter_doc_para_pdf(file)
        assert str(tmp_path) in output
        assert os.path.exists(output)


def test_converter_planilha_para_pdf_honors_config(monkeypatch, app, tmp_path):
    def fake_run(cmd, check=True, timeout=60):
        input_path = cmd[4]
        outdir = cmd[6]
        out = os.path.splitext(os.path.join(outdir, os.path.basename(input_path)))[0] + ".pdf"
        open(out, "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        csv = BytesIO(b"a,b\n1,2")
        file = FileStorage(stream=csv, filename="test.csv")
        output = converter_service.converter_planilha_para_pdf(file)
        assert str(tmp_path) in output
        assert os.path.exists(output)


def test_juntar_pdfs_honors_config(app, tmp_path):
    with app.app_context():
        file1 = FileStorage(stream=_simple_pdf(), filename="f1.pdf")
        file2 = FileStorage(stream=_simple_pdf(), filename="f2.pdf")
        output = merge_service.juntar_pdfs([file1, file2])
        assert str(tmp_path) in output
        assert os.path.exists(output)


def test_dividir_pdf_honors_config(app, tmp_path):
    with app.app_context():
        file = FileStorage(stream=_simple_pdf(page_count=2), filename="split.pdf")
        outputs = split_service.dividir_pdf(file)
        assert len(outputs) == 2
        for out in outputs:
            assert str(tmp_path) in out
            assert os.path.exists(out)

