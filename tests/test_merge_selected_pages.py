from io import BytesIO
from werkzeug.datastructures import FileStorage
from app import create_app
from app.services import merge_service


class DummyPage:
    def __init__(self, ident):
        self.ident = ident


class FakeReader:
    def __init__(self, file):
        self.pages = [DummyPage(f"{file.filename}-p{i}") for i in range(1, 4)]


class FakeWriter:
    def __init__(self):
        self.added = []

    def add_page(self, page):
        self.added.append(page)

    def write(self, f):
        self.out_path = f.name
        open(f.name, "wb").close()


def test_merge_selected_pdfs_respects_pages(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    writer = FakeWriter()
    monkeypatch.setattr(merge_service, "PdfReader", FakeReader)
    monkeypatch.setattr(merge_service, "PdfWriter", lambda: writer)
    with app.app_context():
        f1 = FileStorage(stream=BytesIO(b"a"), filename="a.pdf")
        f2 = FileStorage(stream=BytesIO(b"b"), filename="b.pdf")
        out = merge_service.merge_selected_pdfs([f1, f2], [[2], [1, 3]])
    ids = [p.ident for p in writer.added]
    assert ids == ["a.pdf-p2", "b.pdf-p1", "b.pdf-p3"]
    assert out == writer.out_path
    assert out.startswith(str(tmp_path))
