import io
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from PyPDF2 import PdfWriter

from app import create_app
from app.utils.security import OUTPUT_OWNER_SESSION_KEY


OWNER_A = "a" * 32
OWNER_B = "b" * 32
JOB_A = "1" * 32


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["RATELIMIT_ENABLED"] = False
    app.config["UPLOAD_FOLDER"] = tmp_path
    return app


def _pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _set_owner(client, owner_id=OWNER_A):
    with client.session_transaction() as sess:
        sess[OUTPUT_OWNER_SESSION_KEY] = owner_id


def _generated_rel(owner_id=OWNER_A, job_id=JOB_A, filename="arquivo.pdf"):
    return f"generated/{owner_id}/{job_id}/{filename}"


def _write_generated(upload_folder, owner_id=OWNER_A, job_id=JOB_A, filename="arquivo.pdf"):
    rel_path = _generated_rel(owner_id, job_id, filename)
    abs_path = Path(upload_folder, *rel_path.split("/"))
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(_pdf_bytes())
    return rel_path


def _raw_url(rel_path):
    return f"/viewer/raw/{rel_path}"


def _rel_from_download_url(download_url):
    path = unquote(urlparse(download_url).path)
    prefix = "/viewer/raw/"
    assert path.startswith(prefix)
    return path[len(prefix):]


def test_owner_session_can_view_its_generated_file(app):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _write_generated(app.config["UPLOAD_FOLDER"], OWNER_A, JOB_A)

    first = client_a.get(_raw_url(rel_path))
    second = client_a.get(_raw_url(rel_path))

    assert first.status_code == 200
    assert first.mimetype == "application/pdf"
    assert second.status_code == 200


def test_different_session_cannot_use_same_viewer_url(app):
    client_a = app.test_client()
    client_b = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _write_generated(app.config["UPLOAD_FOLDER"], OWNER_A, JOB_A)

    assert client_a.get(_raw_url(rel_path)).status_code == 200
    assert client_b.get(_raw_url(rel_path)).status_code == 404


def test_leaked_full_path_is_not_enough_without_owner_cookie(app):
    client_b = app.test_client()
    rel_path = _write_generated(app.config["UPLOAD_FOLDER"], OWNER_A, JOB_A)

    assert client_b.get(_raw_url(rel_path)).status_code == 404


def test_session_owner_must_match_path_owner(app):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _write_generated(app.config["UPLOAD_FOLDER"], OWNER_B, JOB_A)

    assert client_a.get(_raw_url(rel_path)).status_code == 404


@pytest.mark.parametrize(
    "job_id",
    [
        "short",
        "g" * 32,
        f"{JOB_A}/..",
    ],
)
def test_invalid_job_id_returns_404(app, job_id):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _generated_rel(OWNER_A, job_id, "arquivo.pdf")

    assert client_a.get(_raw_url(rel_path)).status_code == 404


@pytest.mark.parametrize(
    "rel_path",
    [
        "../arquivo.pdf",
        "%2e%2e/arquivo.pdf",
        f"generated/{OWNER_A}/{JOB_A}/../../arquivo.pdf",
    ],
)
def test_path_traversal_returns_404(app, rel_path):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)

    assert client_a.get(_raw_url(rel_path)).status_code == 404


def test_existing_file_outside_generated_is_not_public(app):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    Path(app.config["UPLOAD_FOLDER"], "solto.pdf").write_bytes(_pdf_bytes())

    assert client_a.get("/viewer/raw/solto.pdf").status_code == 404


def test_viewer_containment_log_does_not_expose_paths(app, tmp_path, monkeypatch, caplog):
    from app.routes import viewer as viewer_routes

    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _generated_rel(OWNER_A, JOB_A, "leak.pdf")
    outside = tmp_path.parent / f"{tmp_path.name}_outside.pdf"

    original_realpath = viewer_routes.os.path.realpath

    def fake_realpath(path):
        normalized = str(path).replace("\\", "/")
        if normalized.endswith(rel_path):
            return str(outside)
        return original_realpath(path)

    monkeypatch.setattr(viewer_routes.os.path, "realpath", fake_realpath)
    monkeypatch.setattr(viewer_routes.os.path, "isfile", lambda _path: True)

    caplog.set_level(logging.WARNING, logger="app.routes.viewer")
    response = client_a.get(_raw_url(rel_path))
    assert response.status_code == 404

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[viewer] acesso bloqueado por contencao de caminho" in log_text
    assert str(app.config["UPLOAD_FOLDER"]) not in log_text
    assert str(outside) not in log_text
    assert OWNER_A not in log_text
    assert JOB_A not in log_text
    assert "leak.pdf" not in log_text


def test_valid_owned_path_for_missing_file_returns_404(app):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _generated_rel(OWNER_A, JOB_A, "ausente.pdf")

    assert client_a.get(_raw_url(rel_path)).status_code == 404


def test_viewer_page_uses_owned_generated_file_and_keeps_security_headers(app):
    client_a = app.test_client()
    _set_owner(client_a, OWNER_A)
    rel_path = _write_generated(app.config["UPLOAD_FOLDER"], OWNER_A, JOB_A)

    page = client_a.get(f"/viewer/{rel_path}")
    raw = client_a.get(_raw_url(rel_path))

    assert page.status_code == 200
    assert b"Visualizador de PDF" in page.data
    assert raw.status_code == 200
    assert raw.headers["X-Content-Type-Options"] == "nosniff"


def test_converter_outputs_are_namespaced_and_session_isolated(app, monkeypatch):
    from app.routes import converter as converter_routes

    def fake_convert(upload_file, target, out_dir):
        ext = "pdf" if target == "pdf" else target
        name = Path(upload_file.filename or "arquivo").stem or "arquivo"
        out_path = Path(out_dir) / f"{name}.{ext}"
        out_path.write_bytes(_pdf_bytes())
        return str(out_path)

    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    client_a = app.test_client()
    client_b = app.test_client()
    response = client_a.post(
        "/api/convert/to-pdf",
        data={
            "files[]": [
                (io.BytesIO(_pdf_bytes()), "entrada-a.pdf"),
                (io.BytesIO(_pdf_bytes()), "entrada-b.pdf"),
            ]
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    assert len(payload["files"]) == 2

    rel_paths = [_rel_from_download_url(item["download_url"]) for item in payload["files"]]
    owners = {rel_path.split("/")[1] for rel_path in rel_paths}
    jobs = {rel_path.split("/")[2] for rel_path in rel_paths}

    assert len(owners) == 1
    assert len(jobs) == 2
    assert all(rel_path.startswith("generated/") for rel_path in rel_paths)
    assert all(item["name"] and isinstance(item["size"], int) for item in payload["files"])

    with client_a.session_transaction() as sess:
        assert sess[OUTPUT_OWNER_SESSION_KEY] in owners
        assert "owned_outputs" not in sess

    for item in payload["files"]:
        assert client_a.get(item["download_url"]).status_code == 200
        assert client_b.get(item["download_url"]).status_code == 404


def test_converter_post_still_requires_csrf_by_default(tmp_path):
    csrf_app = create_app()
    csrf_app.config["TESTING"] = True
    csrf_app.config["RATELIMIT_ENABLED"] = False
    csrf_app.config["UPLOAD_FOLDER"] = tmp_path

    response = csrf_app.test_client().post(
        "/api/convert/to-pdf",
        data={},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "CSRF"
