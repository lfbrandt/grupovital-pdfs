import io
import re
from PyPDF2 import PdfWriter
from app import create_app


def _simple_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def _get_csrf_token(client):
    page = client.get('/compress', base_url='https://localhost')
    html = page.get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def test_compress_success(monkeypatch, tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    client = app.test_client()
    token = _get_csrf_token(client)

    def fake_run(cmd, check=True, timeout=60):
        for part in cmd:
            if str(part).startswith('-sOutputFile='):
                out = part.split('=', 1)[1]
                with open(out, 'wb') as f:
                    f.write(b'%PDF-1.4 fake')
                break

    monkeypatch.setattr('subprocess.run', fake_run)

    data = {'file': (_simple_pdf(), 'input.pdf')}
    resp = client.post(
        '/api/compress',
        data=data,
        content_type='multipart/form-data',
        headers={'X-CSRFToken': token, 'Referer': 'https://localhost/compress'},
        base_url='https://localhost'
    )

    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'
    assert resp.data.startswith(b'%PDF')
    assert list(tmp_path.iterdir()) == []


def test_compress_missing_file():
    app = create_app()
    client = app.test_client()
    token = _get_csrf_token(client)

    resp = client.post(
        '/api/compress',
        data={},
        headers={'X-CSRFToken': token, 'Referer': 'https://localhost/compress'},
        base_url='https://localhost'
    )

    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Nenhum arquivo enviado.'}


def test_compress_empty_filename():
    app = create_app()
    client = app.test_client()
    token = _get_csrf_token(client)

    data = {'file': (io.BytesIO(b''), '')}
    resp = client.post(
        '/api/compress',
        data=data,
        content_type='multipart/form-data',
        headers={'X-CSRFToken': token, 'Referer': 'https://localhost/compress'},
        base_url='https://localhost'
    )

    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'Nenhum arquivo selecionado.'}


def test_compress_invalid_extension():
    app = create_app()
    client = app.test_client()
    token = _get_csrf_token(client)

    data = {'file': (io.BytesIO(b'test'), 'bad.txt')}
    resp = client.post(
        '/api/compress',
        data=data,
        content_type='multipart/form-data',
        headers={'X-CSRFToken': token, 'Referer': 'https://localhost/compress'},
        base_url='https://localhost'
    )

    assert resp.status_code == 500
    assert resp.get_json() == {'error': 'Apenas arquivos PDF s\u00e3o permitidos.'}
