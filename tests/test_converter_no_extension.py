import io
from app import create_app


def test_convert_without_extension_returns_400():
    app = create_app()
    client = app.test_client()
    data = {
        'file': (io.BytesIO(b'test'), 'noextension')
    }
    resp = client.post('/api/convert', data=data, content_type='multipart/form-data')
    assert resp.status_code == 400
