from io import BytesIO
from PyPDF2 import PdfWriter
from app import create_app


def _pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf.getvalue()


def test_viewer_route_returns_html(tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    filename = "test.pdf"
    with open(tmp_path / filename, "wb") as f:
        f.write(_pdf_bytes())

    client = app.test_client()
    resp = client.get(f"/viewer/{filename}")
    assert resp.status_code == 200
    assert b"Visualizador de PDF" in resp.data


def test_viewer_raw_serves_file(tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    filename = "raw.pdf"
    pdf_path = tmp_path / filename
    with open(pdf_path, "wb") as f:
        f.write(_pdf_bytes())

    client = app.test_client()
    resp = client.get(f"/viewer/raw/{filename}")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"


def test_viewer_missing_returns_404(tmp_path):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    client = app.test_client()
    resp = client.get("/viewer/no.pdf")
    assert resp.status_code == 404
