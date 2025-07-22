import io
import re
from app import create_app


def test_oversized_upload_returns_413(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "testing")

    app = create_app()
    app.config["MAX_CONTENT_LENGTH"] = 10
    client = app.test_client()

    page = client.get("/converter", base_url="https://localhost")
    html = page.get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    data = {"file": (io.BytesIO(b"x" * 20), "big.pdf")}
    resp = client.post(
        "/api/pdf/convert",
        data=data,
        content_type="multipart/form-data",
        headers={"X-CSRFToken": token, "Referer": "https://localhost/converter"},
        base_url="https://localhost",
    )
    assert resp.status_code == 413
    assert resp.get_json() == {"error": "Arquivo muito grande."}
