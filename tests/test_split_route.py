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


def test_split_endpoint_success(monkeypatch, tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    def fake_split(file):
        p1 = tmp_path / "p1.pdf"
        p2 = tmp_path / "p2.pdf"
        p1.write_bytes(b"A")
        p2.write_bytes(b"B")
        return [str(p1), str(p2)]

    monkeypatch.setattr('app.routes.split.dividir_pdf', fake_split)
    monkeypatch.setattr('os.remove', lambda *args, **kwargs: None)

    data = {'file': (_simple_pdf(), 'doc.pdf')}
    resp = client.post('/api/split', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200


def test_split_endpoint_requires_file():
    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    resp = client.post('/api/split', data={}, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Nenhum arquivo enviado.'}


def test_split_endpoint_requires_filename():
    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()
    data = {'file': (_simple_pdf(), '')}
    resp = client.post('/api/split', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Nenhum arquivo selecionado.'}

