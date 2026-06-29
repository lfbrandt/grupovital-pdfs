from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from app.services import split_service
from tests.pdf_fixture_factory import make_synthetic_pdf


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["RATELIMIT_ENABLED"] = False
    app.config["UPLOAD_FOLDER"] = tmp_path
    return app


@pytest.fixture
def fixture_pdf(tmp_path):
    return make_synthetic_pdf(tmp_path / "split_sanitize_source.pdf")


def _as_filestorage(path: Path, filename: str = "confidential-upload.pdf") -> FileStorage:
    return FileStorage(stream=io.BytesIO(path.read_bytes()), filename=filename)


def _assert_no_split_temporaries(upload_folder: Path) -> None:
    patterns = (
        "safe_*.pdf",
        "selecionadas_*.pdf",
        "pagina_*.pdf",
        "*.zip",
        "*confidential-upload*.pdf",
    )
    leftovers = []
    for pattern in patterns:
        leftovers.extend(upload_folder.glob(pattern))
    assert leftovers == []


def test_dividir_pdf_fails_closed_when_preserving_sanitize_fails(
    app, fixture_pdf, tmp_path, monkeypatch
):
    sensitive_message = (
        r"sanitize failed at C:\secret\absolute\confidential-upload.pdf "
        "with CLIENT_SECRET_VALUE"
    )

    def fail_sanitize(_input_path, output_path):
        Path(output_path).write_bytes(b"partial sanitize output")
        raise RuntimeError(sensitive_message)

    def fail_if_counted(_path):
        pytest.fail("original PDF was counted after sanitize failure")

    def fail_if_written(*_args, **_kwargs):
        pytest.fail("split output was written after sanitize failure")

    monkeypatch.setattr(split_service, "sanitize_pdf_preserving_content", fail_sanitize)
    monkeypatch.setattr(split_service, "_page_count", fail_if_counted)
    monkeypatch.setattr(split_service, "write_preserving_pdf_subset", fail_if_written)

    with app.app_context(), pytest.raises(RuntimeError, match="split_sanitize_failed"):
        split_service.dividir_pdf(_as_filestorage(fixture_pdf))

    _assert_no_split_temporaries(tmp_path)


def test_split_route_sanitization_failure_is_controlled_and_non_sensitive(
    app, fixture_pdf, tmp_path, monkeypatch, caplog
):
    sensitive_message = (
        r"sanitize failed at C:\secret\absolute\confidential-upload.pdf "
        "with CLIENT_SECRET_VALUE"
    )
    write_calls = []
    count_calls = []

    def fail_sanitize(_input_path, output_path):
        Path(output_path).write_bytes(b"partial sanitize output")
        raise RuntimeError(sensitive_message)

    def track_count(_path):
        count_calls.append("called")
        raise AssertionError("original PDF was counted after sanitize failure")

    def track_write(*_args, **_kwargs):
        write_calls.append("called")
        raise AssertionError("split output was written after sanitize failure")

    monkeypatch.setattr(split_service, "sanitize_pdf_preserving_content", fail_sanitize)
    monkeypatch.setattr(split_service, "_page_count", track_count)
    monkeypatch.setattr(split_service, "write_preserving_pdf_subset", track_write)

    caplog.set_level(logging.ERROR, logger="app")
    caplog.clear()

    response = app.test_client().post(
        "/api/split",
        data={
            "file": (
                io.BytesIO(fixture_pdf.read_bytes()),
                "confidential-upload.pdf",
            )
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 500
    assert response.get_json() == {"error": "Falha ao dividir o PDF."}
    assert count_calls == []
    assert write_calls == []
    _assert_no_split_temporaries(tmp_path)

    response_text = response.get_data(as_text=True)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[split] falha na sanitizacao" in log_text
    assert "RuntimeError" in log_text
    assert "[split] falha controlada" in log_text

    for forbidden in (
        sensitive_message,
        "CLIENT_SECRET_VALUE",
        "confidential-upload.pdf",
        str(app.config["UPLOAD_FOLDER"]),
        str(fixture_pdf),
        r"C:\secret\absolute",
        "Traceback",
        'File "',
    ):
        assert forbidden not in log_text
        assert forbidden not in response_text
