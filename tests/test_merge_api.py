import json
import re
from io import BytesIO

from PyPDF2 import PdfReader, PdfWriter

from app import create_app


def _pdf_bytes(page_sizes):
    writer = PdfWriter()
    for width, height in page_sizes:
        writer.add_blank_page(width=width, height=height)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _app_client(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = tmp_path
    return app.test_client()


def _csrf_token(client):
    page = client.get("/merge", base_url="https://localhost")
    html = page.get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def _post_merge(client, data):
    token = _csrf_token(client)
    return client.post(
        "/api/merge",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRFToken": token, "Referer": "https://localhost/merge"},
        base_url="https://localhost",
    )


def _page_sizes(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    return [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in reader.pages
    ]


def test_merge_legacy_keeps_byte_identical_pdfs_as_independent_inputs(tmp_path):
    client = _app_client(tmp_path)
    pdf = _pdf_bytes([(200, 300), (220, 320)])

    resp = _post_merge(
        client,
        {
            "files": [
                (BytesIO(pdf), "a.pdf"),
                (BytesIO(pdf), "b.pdf"),
            ],
            "pagesMap": json.dumps([[1, 2], [1, 2]]),
            "flatten": "false",
            "normalize": "off",
        },
    )

    assert resp.status_code == 200
    assert _page_sizes(resp.data) == [
        (200.0, 300.0),
        (220.0, 320.0),
        (200.0, 300.0),
        (220.0, 320.0),
    ]


def test_merge_modern_plan_can_repeat_same_file_and_pages(tmp_path):
    client = _app_client(tmp_path)
    first = _pdf_bytes([(200, 300), (220, 320)])
    second = _pdf_bytes([(240, 340)])

    resp = _post_merge(
        client,
        {
            "files": [
                (BytesIO(first), "first.pdf"),
                (BytesIO(second), "second.pdf"),
            ],
            "plan": json.dumps([
                {"src": 0, "page": 1, "rotation": 0},
                {"src": 0, "page": 1, "rotation": 0},
                {"src": 0, "page": 2, "rotation": 0},
                {"src": 1, "page": 1, "rotation": 0},
            ]),
            "flatten": "false",
            "normalize": "off",
        },
    )

    assert resp.status_code == 200
    assert _page_sizes(resp.data) == [
        (200.0, 300.0),
        (200.0, 300.0),
        (220.0, 320.0),
        (240.0, 340.0),
    ]


def test_merge_legacy_different_files_keeps_existing_behavior(tmp_path):
    client = _app_client(tmp_path)
    first = _pdf_bytes([(200, 300)])
    second = _pdf_bytes([(220, 320), (240, 340)])

    resp = _post_merge(
        client,
        {
            "files": [
                (BytesIO(first), "first.pdf"),
                (BytesIO(second), "second.pdf"),
            ],
            "pagesMap": json.dumps([[1], [1, 2]]),
            "flatten": "false",
            "normalize": "off",
        },
    )

    assert resp.status_code == 200
    assert _page_sizes(resp.data) == [
        (200.0, 300.0),
        (220.0, 320.0),
        (240.0, 340.0),
    ]


def test_merge_single_file_keeps_current_contract(tmp_path):
    client = _app_client(tmp_path)
    pdf = _pdf_bytes([(200, 300)])

    resp = _post_merge(
        client,
        {
            "files": [(BytesIO(pdf), "only.pdf")],
            "pagesMap": json.dumps([[1]]),
        },
    )

    assert resp.status_code == 422
    assert resp.get_json() == {"error": "Envie ao menos 2 arquivos PDF."}


def test_merge_file_count_limit_is_unchanged(tmp_path, monkeypatch):
    client = _app_client(tmp_path)
    monkeypatch.setenv("LIMIT_MAX_MERGE_FILES", "2")
    pdf = _pdf_bytes([(200, 300)])

    resp = _post_merge(
        client,
        {
            "files": [
                (BytesIO(pdf), "one.pdf"),
                (BytesIO(pdf), "two.pdf"),
                (BytesIO(pdf), "three.pdf"),
            ],
            "pagesMap": json.dumps([[1], [1], [1]]),
        },
    )

    assert resp.status_code == 422
    assert "Muitos arquivos (3). Limite: 2." in resp.get_json()["error"]


def test_merge_total_page_limit_is_unchanged(tmp_path, monkeypatch):
    client = _app_client(tmp_path)
    monkeypatch.setenv("MAX_TOTAL_PAGES", "1")
    first = _pdf_bytes([(200, 300)])
    second = _pdf_bytes([(220, 320)])

    resp = _post_merge(
        client,
        {
            "files": [
                (BytesIO(first), "first.pdf"),
                (BytesIO(second), "second.pdf"),
            ],
            "pagesMap": json.dumps([[1], [1]]),
        },
    )

    assert resp.status_code == 422
    assert "acima do limite global de 1" in resp.get_json()["error"]
