import json
from io import BytesIO
from PyPDF2 import PdfWriter, PdfReader
from app import create_app


def _three_page_pdf():
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=10, height=10)
    buf = BytesIO()
    w.write(buf)
    buf.seek(0)
    return buf


def test_preview_generates_images(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    data = {'file': (_three_page_pdf(), 'a.pdf')}
    resp = client.post('/api/pdf/preview', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    info = resp.get_json()
    assert 'pages' in info
    assert len(info['pages']) == 3
    for url in info['pages']:
        img_resp = client.get(url)
        assert img_resp.status_code == 200
        assert img_resp.mimetype == 'image/png'


def test_compress_applies_mods(monkeypatch, tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    def fake_run(cmd, check=True, timeout=60):
        out = None
        for part in cmd:
            if str(part).startswith('-sOutputFile='):
                out = part.split('=', 1)[1]
        src = cmd[-1]
        if out:
            import shutil
            shutil.copyfile(src, out)

    monkeypatch.setattr('subprocess.run', fake_run)

    mods = {"removed": [1], "rotations": {"2": 90}}
    data = {
        'file': (_three_page_pdf(), 'b.pdf'),
        'mods': json.dumps(mods)
    }
    resp = client.post('/api/pdf/compress', data=data, content_type='multipart/form-data')
    assert resp.status_code == 200
    reader = PdfReader(BytesIO(resp.data))
    assert len(reader.pages) == 2
    rotation = reader.pages[1].get('/Rotate')
    assert rotation in (90, 270)

