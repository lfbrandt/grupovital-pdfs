import re
from io import BytesIO
from PyPDF2 import PdfWriter
from app import create_app


def _simple_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def test_referrer_policy_header():
    app = create_app()
    client = app.test_client()
    resp = client.get("/", base_url="https://localhost")
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_ajax_request_csrf_success():
    app = create_app()
    client = app.test_client()

    page = client.get("/merge", base_url="https://localhost")
    html = page.get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    data = {
        "files": [
            (_simple_pdf(), "a.pdf"),
            (_simple_pdf(), "b.pdf"),
        ],
        "pagesMap": "[[1],[1]]",
    }
    resp = client.post(
        "/api/merge",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRFToken": token, "Referer": "https://localhost/merge"},
        base_url="https://localhost",
    )
    assert resp.status_code == 200
