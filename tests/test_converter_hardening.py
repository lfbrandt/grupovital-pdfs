import io
import logging
import re
from pathlib import Path

import pytest
from PyPDF2 import PdfWriter

from app import create_app
from app.utils.security import OUTPUT_OWNER_SESSION_KEY


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _app(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["RATELIMIT_ENABLED"] = False
    app.config["UPLOAD_FOLDER"] = tmp_path
    return app


def _csrf_token(client):
    page = client.get("/converter", base_url="https://localhost")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_converter_page_without_trailing_slash_exposes_csrf_meta(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()

    page = client.get("/converter", base_url="https://localhost")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert re.search(r'name="csrf-token" content="[^"]+"', html)


def test_converter_post_with_csrf_uses_isolated_tempdir_and_namespaced_output(
    tmp_path, monkeypatch
):
    from app.routes import converter as converter_routes

    app = _app(tmp_path)
    temp_dirs = []

    def fake_convert(upload_file, target, out_dir):
        temp_dir = Path(out_dir)
        temp_dirs.append(temp_dir)
        assert temp_dir.name.startswith("gvpdf_conv_")
        output = temp_dir / f"{Path(upload_file.filename or 'arquivo').stem}.{target}"
        output.write_bytes(_pdf_bytes())
        return str(output)

    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    client = app.test_client()
    token = _csrf_token(client)
    response = client.post(
        "/api/convert/to-pdf",
        data={"files[]": [(io.BytesIO(_pdf_bytes()), "entrada.pdf")]},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": token, "Referer": "https://localhost/converter"},
        base_url="https://localhost",
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["count"] == 1
    rel_path = payload["files"][0]["download_url"].split("/viewer/raw/", 1)[1]
    parts = rel_path.split("/")
    assert parts[0] == "generated"
    with client.session_transaction() as sess:
        assert sess[OUTPUT_OWNER_SESSION_KEY] == parts[1]
    assert client.get(payload["files"][0]["download_url"]).status_code == 200
    assert temp_dirs
    assert all(not temp_dir.exists() for temp_dir in temp_dirs)


@pytest.mark.parametrize(
    ("url", "target", "expected_error", "expected_log"),
    [
        (
            "/api/convert/to-pdf",
            None,
            "Não foi possível converter o arquivo para PDF.",
            "[converter] to-pdf-runtime falhou: RuntimeError",
        ),
        (
            "/api/convert",
            "pdf",
            "Não foi possível converter o arquivo.",
            "[converter] generic-runtime falhou: RuntimeError",
        ),
        (
            "/api/convert/to-xlsx",
            None,
            "Não foi possível converter o arquivo para XLSX.",
            "[converter] to-xlsx-runtime falhou: RuntimeError",
        ),
    ],
)
def test_converter_controlled_runtime_error_is_redacted_in_response_and_log(
    tmp_path, monkeypatch, caplog, url, target, expected_error, expected_log
):
    from app.routes import converter as converter_routes

    app = _app(tmp_path)
    sensitive_path = r"C:\Users\Caio\segredo\contrato-sigiloso.pdf"
    sensitive_filename = "contrato-sigiloso.pdf"
    secret = "CONVERTER_SECRET_123"
    internal_detail = "soffice --headless --convert-to pdf --outdir C:\\tmp\\gvpdf"
    sensitive_message = (
        f"conversion failed at {sensitive_path} for {sensitive_filename}; "
        f"secret={secret}; internal={internal_detail}; Traceback File \"worker.py\""
    )

    def fail_convert(*_args, **_kwargs):
        raise RuntimeError(sensitive_message)

    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fail_convert)

    client = app.test_client()
    token = _csrf_token(client)
    caplog.set_level(logging.WARNING, logger="app")
    caplog.clear()
    form_data = {"files[]": [(io.BytesIO(_pdf_bytes()), sensitive_filename)]}
    if target:
        form_data["target"] = target
    response = client.post(
        url,
        data=form_data,
        content_type="multipart/form-data",
        headers={"X-CSRFToken": token, "Referer": "https://localhost/converter"},
        base_url="https://localhost",
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": expected_error}
    response_text = response.get_data(as_text=True)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert log_text == expected_log
    for forbidden in (
        sensitive_message,
        sensitive_path,
        sensitive_filename,
        secret,
        internal_detail,
        "RuntimeError",
        "Traceback",
        'File "',
    ):
        assert forbidden not in response_text
    for forbidden in (
        sensitive_message,
        sensitive_path,
        sensitive_filename,
        secret,
        internal_detail,
        str(app.config["UPLOAD_FOLDER"]),
        "Traceback",
        'File "',
    ):
        assert forbidden not in log_text
