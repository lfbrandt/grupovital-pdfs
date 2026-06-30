from __future__ import annotations

import json
import logging
import re
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from PyPDF2 import PdfReader, PdfWriter

from app import create_app
from app.routes import merge as merge_routes
from app.services import merge_service


def _pdf_bytes(page_count: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=300)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = tmp_path
    return app


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


def _assert_no_merge_temporaries(upload_folder: Path) -> None:
    leftovers = []
    for pattern in ("tmp*.pdf", "san_*.pdf", "merge_*.pdf", "*.rebuilt.pdf", "*.san.pdf"):
        leftovers.extend(upload_folder.glob(pattern))
    assert leftovers == []


def _consume_response_body(response) -> bytes:
    return b"".join(response.response)


def test_merge_service_fails_closed_when_input_sanitize_fails(
    app, tmp_path, monkeypatch
):
    inputs = []
    for index in range(3):
        path = tmp_path / f"input_{index}.pdf"
        path.write_bytes(_pdf_bytes())
        inputs.append(str(path))

    outside = tmp_path.parent / f"{tmp_path.name}_outside_keep.pdf"
    outside.write_bytes(b"outside")
    sensitive_message = (
        r"sanitize failed at C:\secret\absolute\confidential-merge.pdf "
        "with MERGE_SECRET_VALUE"
    )
    sanitize_calls = []

    def fake_sanitize(input_path, output_path, **_kwargs):
        sanitize_calls.append(input_path)
        if len(sanitize_calls) == 1:
            shutil.copyfile(input_path, output_path)
            return
        if len(sanitize_calls) == 2:
            Path(output_path).write_bytes(b"partial sanitize output")
            raise RuntimeError(sensitive_message)
        pytest.fail("third merge input was sanitized after failure")

    def fail_if_reader_called(*_args, **_kwargs):
        pytest.fail("PDF reader was called after sanitize failure")

    def fail_if_writer_called(*_args, **_kwargs):
        pytest.fail("final merge writer was called after sanitize failure")

    monkeypatch.setattr(merge_service, "sanitize_pdf", fake_sanitize)
    monkeypatch.setattr(merge_service, "PdfReader", fail_if_reader_called)
    monkeypatch.setattr(merge_service, "PdfWriter", fail_if_writer_called)

    with app.app_context(), pytest.raises(RuntimeError, match="merge_sanitize_failed"):
        merge_service.merge_selected_pdfs(inputs, pages_map=[[1], [1], [1]])

    assert sanitize_calls == inputs[:2]
    assert all(Path(path).exists() for path in inputs)
    assert outside.exists()
    _assert_no_merge_temporaries(tmp_path)
    outside.unlink()


@pytest.mark.parametrize("with_plan", [False, True])
def test_merge_route_sanitization_failure_is_atomic_and_non_sensitive(
    app, tmp_path, monkeypatch, caplog, with_plan
):
    sensitive_message = (
        r"sanitize failed at C:\secret\absolute\confidential-merge.pdf "
        "with MERGE_SECRET_VALUE"
    )
    sanitize_calls = []

    def fake_sanitize(input_path, output_path, **_kwargs):
        sanitize_calls.append(input_path)
        if len(sanitize_calls) == 1:
            shutil.copyfile(input_path, output_path)
            return
        if len(sanitize_calls) == 2:
            Path(output_path).write_bytes(b"partial sanitize output")
            raise RuntimeError(sensitive_message)
        pytest.fail("third merge input was sanitized after failure")

    def fail_if_reader_called(*_args, **_kwargs):
        pytest.fail("PDF reader was called after sanitize failure")

    def fail_if_writer_called(*_args, **_kwargs):
        pytest.fail("final merge writer was called after sanitize failure")

    monkeypatch.setattr(merge_service, "sanitize_pdf", fake_sanitize)
    monkeypatch.setattr(merge_service, "PdfReader", fail_if_reader_called)
    monkeypatch.setattr(merge_service, "PdfWriter", fail_if_writer_called)

    data = {
        "files": [
            (BytesIO(_pdf_bytes()), "first-confidential.pdf"),
            (BytesIO(_pdf_bytes()), "second-confidential.pdf"),
            (BytesIO(_pdf_bytes()), "third-confidential.pdf"),
        ],
        "flatten": "false",
        "normalize": "off",
    }
    if with_plan:
        data["plan"] = json.dumps(
            [
                {"src": 0, "page": 1, "rotation": 0},
                {"src": 1, "page": 1, "rotation": 0},
                {"src": 2, "page": 1, "rotation": 0},
            ]
        )
    else:
        data["pagesMap"] = json.dumps([[1], [1], [1]])

    caplog.set_level(logging.ERROR, logger="app")
    caplog.clear()
    response = _post_merge(app.test_client(), data)

    assert response.status_code == 500
    assert response.get_json() == {"error": "Erro interno ao juntar PDFs."}
    assert len(sanitize_calls) == 2
    _assert_no_merge_temporaries(tmp_path)

    response_text = response.get_data(as_text=True)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[merge] falha na sanitizacao" in log_text
    assert "[merge] falha controlada" in log_text
    assert "RuntimeError" in log_text

    for forbidden in (
        sensitive_message,
        "MERGE_SECRET_VALUE",
        "confidential-merge.pdf",
        "first-confidential.pdf",
        "second-confidential.pdf",
        "third-confidential.pdf",
        str(app.config["UPLOAD_FOLDER"]),
        r"C:\secret\absolute",
        "Traceback",
        'File "',
    ):
        assert forbidden not in log_text
        assert forbidden not in response_text


def test_merge_route_fails_closed_when_output_sanitize_fails(
    app, tmp_path, monkeypatch, caplog
):
    sensitive_message = (
        r"output sanitize failed at C:\secret\absolute\confidential-output.pdf "
        "with MERGE_OUTPUT_SECRET"
    )
    sanitize_calls = []

    def fake_sanitize(input_path, output_path, **_kwargs):
        sanitize_calls.append((str(input_path), str(output_path)))
        if str(input_path).endswith(".rebuilt.pdf"):
            Path(output_path).write_bytes(b"partial sanitized output")
            raise RuntimeError(sensitive_message)
        shutil.copyfile(input_path, output_path)

    monkeypatch.setattr(merge_service, "sanitize_pdf", fake_sanitize)

    caplog.set_level(logging.ERROR, logger="app")
    caplog.clear()
    response = _post_merge(
        app.test_client(),
        {
            "files": [
                (BytesIO(_pdf_bytes()), "first-confidential.pdf"),
                (BytesIO(_pdf_bytes()), "second-confidential.pdf"),
            ],
            "pagesMap": json.dumps([[1], [1]]),
            "flatten": "false",
            "normalize": "off",
        },
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Erro interno ao juntar PDFs."}
    assert not response.data.startswith(b"%PDF")
    assert any(call[0].endswith(".rebuilt.pdf") for call in sanitize_calls)
    _assert_no_merge_temporaries(tmp_path)

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[merge_service] sanitize final falhou" in log_text
    assert "[merge] falha controlada" in log_text
    assert "RuntimeError" in log_text
    for forbidden in (
        sensitive_message,
        "MERGE_OUTPUT_SECRET",
        "confidential-output.pdf",
        "first-confidential.pdf",
        "second-confidential.pdf",
        str(app.config["UPLOAD_FOLDER"]),
        r"C:\secret\absolute",
        "Traceback",
        'File "',
        "usando rebuilt",
    ):
        assert forbidden not in log_text
        assert forbidden not in response.get_data(as_text=True)


def test_merge_success_cleanup_runs_after_response_close(app, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside_keep.pdf"
    outside.write_bytes(b"outside")

    with app.test_request_context(
        "/api/merge",
        method="POST",
        data={
            "files": [
                (BytesIO(_pdf_bytes()), "a.pdf"),
                (BytesIO(_pdf_bytes()), "b.pdf"),
            ],
            "pagesMap": json.dumps([[1], [1]]),
            "flatten": "false",
            "normalize": "off",
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/pdf"},
        base_url="https://localhost",
    ):
        response = merge_routes.merge_api()

    assert response.status_code == 200, response.get_data(as_text=True)
    body = _consume_response_body(response)
    assert body.startswith(b"%PDF")
    assert len(PdfReader(BytesIO(body)).pages) == 2

    merge_outputs = list(tmp_path.glob("merge_*.pdf"))
    assert merge_outputs
    assert all(path.exists() for path in merge_outputs)
    assert outside.exists()

    response.close()

    assert not any(path.exists() for path in merge_outputs)
    assert outside.exists()
    outside.unlink()
