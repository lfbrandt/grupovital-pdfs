from io import BytesIO
from werkzeug.datastructures import FileStorage
from PyPDF2 import PdfWriter

from app import create_app
from app.services import compress_service


def _simple_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_windows_search_picks_highest(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    monkeypatch.delenv("GHOSTSCRIPT_BIN", raising=False)
    monkeypatch.setattr(compress_service.platform, "system", lambda: "Windows")

    paths = [
        r"C:\\Program Files\\gs\\gs9.56.1\\bin\\gswin64c.exe",
        r"C:\\Program Files\\gs\\gs10.0.0\\bin\\gswin64c.exe",
    ]
    monkeypatch.setattr(compress_service.glob, "glob", lambda pattern: paths)

    called = {}

    def fake_run(cmd, check=True, timeout=60):
        called["bin"] = cmd[0]
        for part in cmd:
            if str(part).startswith("-sOutputFile="):
                open(part.split("=", 1)[1], "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(), filename="a.pdf")
        compress_service.comprimir_pdf(file)

    assert called["bin"] == paths[1]


def test_windows_search_fallback_to_gs(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    monkeypatch.delenv("GHOSTSCRIPT_BIN", raising=False)
    monkeypatch.setattr(compress_service.platform, "system", lambda: "Windows")
    monkeypatch.setattr(compress_service.glob, "glob", lambda pattern: [])

    called = {}

    def fake_run(cmd, check=True, timeout=60):
        called["bin"] = cmd[0]
        for part in cmd:
            if str(part).startswith("-sOutputFile="):
                open(part.split("=", 1)[1], "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(), filename="b.pdf")
        compress_service.comprimir_pdf(file)

    assert called["bin"] == "gs"
