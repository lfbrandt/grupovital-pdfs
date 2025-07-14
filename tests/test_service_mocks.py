from io import BytesIO
import os
from werkzeug.datastructures import FileStorage

from app import create_app
from app.services import (
    merge_service,
    split_service,
    compress_service,
    converter_service,
)


def _simple_pdf(page_count=1):
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_extrair_paginas_pdf_uses_mock(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    class FakeReader:
        def __init__(self, path):
            self.pages = ["a", "b", "c"]

    recorded = {}

    class FakeWriter:
        def __init__(self):
            recorded["obj"] = self
            self.pages = []

        def add_page(self, page):
            self.pages.append(page)

        def write(self, fh):
            fh.write(b"data")

    monkeypatch.setattr(merge_service, "PdfReader", FakeReader)
    monkeypatch.setattr(merge_service, "PdfWriter", FakeWriter)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(), filename="t.pdf")
        out = merge_service.extrair_paginas_pdf(file, [1, 3])
        assert os.path.exists(out)
    assert recorded["obj"].pages == ["a", "c"]


def test_dividir_pdf_uses_mock(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    class FakeReader:
        def __init__(self, path):
            self.pages = ["p1", "p2"]

    writers = []

    class FakeWriter:
        def __init__(self):
            writers.append(self)
            self.pages = []

        def add_page(self, page):
            self.pages.append(page)

        def write(self, fh):
            fh.write(b"x")

    monkeypatch.setattr(split_service, "PdfReader", FakeReader)
    monkeypatch.setattr(split_service, "PdfWriter", FakeWriter)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(page_count=2), filename="s.pdf")
        outputs = split_service.dividir_pdf(file)

    assert len(outputs) == 2
    for path in outputs:
        assert os.path.exists(path)
    assert writers[0].pages == ["p1"]
    assert writers[1].pages == ["p2"]


def test_compress_invokes_subprocess(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    called = {}

    def fake_run(cmd, check=True, timeout=60):
        called["cmd"] = cmd
        for part in cmd:
            if str(part).startswith("-sOutputFile="):
                open(part.split("=", 1)[1], "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(), filename="a.pdf")
        compress_service.comprimir_pdf(file)

    assert "-sDEVICE=pdfwrite" in called["cmd"]


def test_converter_planilha_invokes_libreoffice(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    called = {}

    def fake_run(cmd, check=True, timeout=60):
        called["cmd"] = cmd
        input_path = cmd[4]
        outdir = cmd[6]
        out = os.path.splitext(os.path.join(outdir, os.path.basename(input_path)))
        out = out[0] + ".pdf"
        open(out, "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        csv = BytesIO(b"a,b\n1,2")
        file = FileStorage(stream=csv, filename="t.csv")
        converter_service.converter_planilha_para_pdf(file)

    assert "--convert-to" in called["cmd"]


def test_converter_doc_invokes_libreoffice(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    called = {}

    def fake_run(cmd, check=True, timeout=60):
        called["cmd"] = cmd
        input_path = cmd[4]
        outdir = cmd[6]
        out = os.path.splitext(os.path.join(outdir, os.path.basename(input_path)))
        out = out[0] + ".pdf"
        open(out, "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        doc = BytesIO(b"data")
        file = FileStorage(stream=doc, filename="test.docx")
        converter_service.converter_doc_para_pdf(file)

    assert "--convert-to" in called["cmd"]
