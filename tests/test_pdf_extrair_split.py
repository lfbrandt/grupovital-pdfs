import os
from io import BytesIO
from werkzeug.datastructures import FileStorage
from app import create_app
from app.services import merge_service, split_service


def _fake_pdf_data():
    return BytesIO(b"pdf")


class DummyPage:
    pass


class FakeReader:
    def __init__(self, path):
        self.pages = [DummyPage(), DummyPage(), DummyPage()]
        self.path = path


class FakeWriter:
    def __init__(self):
        self.added = []

    def add_page(self, page):
        self.added.append(page)

    def write(self, f):
        self.out_path = f.name
        open(f.name, "wb").close()


def test_extrair_paginas_pdf(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    reader = FakeReader
    writer = FakeWriter()

    monkeypatch.setattr(merge_service, "PdfReader", reader)
    monkeypatch.setattr(merge_service, "PdfWriter", lambda: writer)

    with app.app_context():
        f = FileStorage(stream=_fake_pdf_data(), filename="a.pdf")
        out = merge_service.extrair_paginas_pdf(f, [1, 3])

    assert len(writer.added) == 2
    assert os.path.exists(out)
    assert writer.out_path == out


class FakeSplitReader:
    def __init__(self, path):
        self.pages = [DummyPage(), DummyPage()]
        self.path = path


class FakeSplitWriter:
    def __init__(self):
        self.pages = []

    def add_page(self, page):
        self.pages.append(page)

    def write(self, f):
        self.out = f.name
        open(f.name, "wb").close()


def test_dividir_pdf(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    monkeypatch.setattr(split_service, "PdfReader", FakeSplitReader)
    monkeypatch.setattr(split_service, "PdfWriter", lambda: FakeSplitWriter())

    with app.app_context():
        f = FileStorage(stream=_fake_pdf_data(), filename="b.pdf")
        outputs = split_service.dividir_pdf(f)

    assert len(outputs) == 2
    for path in outputs:
        assert os.path.exists(path)
