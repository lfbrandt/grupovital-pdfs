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


def test_merge_endpoint_success(monkeypatch, tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    def fake_merge(files):
        out = tmp_path / "merged.pdf"
        out.write_bytes(b"PDF")
        return str(out)

    monkeypatch.setattr('app.routes.merge.juntar_pdfs', fake_merge)
    monkeypatch.setattr('os.remove', lambda *args, **kwargs: None)

    data = {
        'files': [
            (_simple_pdf(), 'a.pdf'),
            (_simple_pdf(), 'b.pdf'),
        ]
    }
    resp = client.post('/api/merge', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200


def test_merge_endpoint_requires_files():
    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    resp = client.post('/api/merge', data={}, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Nenhum arquivo enviado.'}


def test_merge_endpoint_needs_multiple_files():
    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    data = {'files': [(BytesIO(b'x'), 'single.pdf')]}
    resp = client.post('/api/merge', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Envie pelo menos dois arquivos PDF.'}


