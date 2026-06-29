import os
from io import BytesIO
from PyPDF2 import PdfWriter
from app import create_app
from app.utils.security import OUTPUT_OWNER_SESSION_KEY


OWNER_ID = "a" * 32
JOB_ID = "1" * 32


def _pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf.getvalue()


def _set_owner(client, owner_id=OWNER_ID):
    with client.session_transaction() as sess:
        sess[OUTPUT_OWNER_SESSION_KEY] = owner_id


def _write_generated_pdf(upload_folder, filename):
    rel_path = f"generated/{OWNER_ID}/{JOB_ID}/{filename}"
    abs_path = upload_folder / "generated" / OWNER_ID / JOB_ID / filename
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(abs_path, 'wb') as f:
        f.write(_pdf_bytes())
    return rel_path


def test_viewer_route_returns_html(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    filename = 'test.pdf'
    rel_path = _write_generated_pdf(tmp_path, filename)

    client = app.test_client()
    _set_owner(client)
    resp = client.get(f'/viewer/{rel_path}')
    assert resp.status_code == 200
    assert b'Visualizador de PDF' in resp.data


def test_viewer_raw_serves_file(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    filename = 'raw.pdf'
    rel_path = _write_generated_pdf(tmp_path, filename)

    client = app.test_client()
    _set_owner(client)
    resp = client.get(f'/viewer/raw/{rel_path}')
    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'


def test_viewer_missing_returns_404(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    client = app.test_client()
    _set_owner(client)
    resp = client.get(f'/viewer/generated/{OWNER_ID}/{JOB_ID}/no.pdf')
    assert resp.status_code == 404

