import os
from io import BytesIO
from werkzeug.datastructures import FileStorage
from app import create_app
from app.services import compress_service, converter_service
from PyPDF2 import PdfWriter


def _simple_pdf():
    w = PdfWriter()
    w.add_blank_page(width=10, height=10)
    buf = BytesIO()
    w.write(buf)
    buf.seek(0)
    return buf


def test_compress_runs_ghostscript(monkeypatch, tmp_path):
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

    assert called["cmd"][0] == "gs"
    assert "-dPDFSETTINGS=/ebook" in called["cmd"]


def test_planilha_uses_libreoffice(monkeypatch, tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    called = {}

    def fake_run(cmd, check=True, timeout=120):
        called["cmd"] = cmd
        out = (
            os.path.splitext(os.path.join(cmd[6], os.path.basename(cmd[4])))[0] + ".pdf"
        )
        open(out, "wb").close()

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        csv = BytesIO(b"a,b\n1,2")
        file = FileStorage(stream=csv, filename="t.csv")
        converter_service.converter_planilha_para_pdf(file)

    assert "--headless" in called["cmd"]
    assert called["cmd"][0] == "libreoffice"
