import base64
import io
import logging
import os
import re
from pathlib import Path

import pytest
from PyPDF2 import PdfWriter

from app import create_app
from app.utils.security import OUTPUT_OWNER_SESSION_KEY


OWNER_A = "a" * 32
OWNER_B = "b" * 32
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


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
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _set_owner(client, owner_id=OWNER_A):
    with client.session_transaction() as sess:
        sess[OUTPUT_OWNER_SESSION_KEY] = owner_id


def _owner_id(client):
    with client.session_transaction() as sess:
        return sess[OUTPUT_OWNER_SESSION_KEY]


def _upload_pdf(client):
    response = client.post(
        "/api/edit/upload",
        data={"file": (io.BytesIO(_pdf_bytes()), "entrada.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _session_dir(upload_folder, owner_id, edit_session_id):
    return Path(upload_folder, "edit_sessions", owner_id, edit_session_id)


def _caplog_text(caplog):
    return "\n".join(record.getMessage() for record in caplog.records)


def test_upload_creates_owner_namespaced_edit_session(app):
    client = app.test_client()

    payload = _upload_pdf(client)
    edit_session_id = payload["session_id"]
    owner_id = _owner_id(client)
    session_dir = _session_dir(app.config["UPLOAD_FOLDER"], owner_id, edit_session_id)

    assert SESSION_RE.fullmatch(edit_session_id)
    assert SESSION_RE.fullmatch(owner_id)
    assert session_dir.is_dir()
    assert (session_dir / "original.pdf").is_file()
    assert (session_dir / "current.pdf").is_file()
    assert (session_dir / "meta.json").is_file()
    assert not Path(app.config["UPLOAD_FOLDER"], "edit_sessions", edit_session_id).exists()
    assert "output_owner_id" not in payload
    assert str(app.config["UPLOAD_FOLDER"]) not in str(payload)


def test_edit_upload_logs_do_not_expose_filename_owner_session_or_path(app, caplog):
    client = app.test_client()
    original_name = "contrato-super-secreto.pdf"

    caplog.set_level(logging.DEBUG, logger="app")
    response = client.post(
        "/api/edit/upload",
        data={"file": (io.BytesIO(_pdf_bytes()), original_name)},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    edit_session_id = response.get_json()["session_id"]
    owner_id = _owner_id(client)
    log_text = _caplog_text(caplog)

    assert original_name not in log_text
    assert edit_session_id not in log_text
    assert owner_id not in log_text
    assert str(app.config["UPLOAD_FOLDER"]) not in log_text


def test_owner_can_access_download_preview_page_image_and_close(app):
    client = app.test_client()
    payload = _upload_pdf(client)
    edit_session_id = payload["session_id"]
    owner_id = _owner_id(client)
    session_dir = _session_dir(app.config["UPLOAD_FOLDER"], owner_id, edit_session_id)

    file_response = client.get(f"/api/edit/file/{edit_session_id}")
    download_response = client.get(f"/api/edit/download/{edit_session_id}")
    page_image_response = client.get(f"/api/edit/page-image/{edit_session_id}/1?scale=0.5")

    assert file_response.status_code == 200
    assert file_response.mimetype == "application/pdf"
    assert download_response.status_code == 200
    assert download_response.mimetype == "application/pdf"
    assert page_image_response.status_code == 200
    assert page_image_response.mimetype == "image/png"
    file_response.close()
    download_response.close()
    page_image_response.close()

    close_response = client.post(
        "/api/edit/close",
        json={"session_id": edit_session_id},
        headers={"Accept": "application/json"},
    )

    assert close_response.status_code == 200
    assert close_response.get_json() == {"ok": True, "session_id": edit_session_id}
    assert not session_dir.exists()
    assert client.get(f"/api/edit/file/{edit_session_id}").status_code == 404


def test_close_and_unauthorized_attempt_logs_do_not_expose_full_ids(app, caplog):
    client_a = app.test_client()
    client_b = app.test_client()
    edit_session_id = _upload_pdf(client_a)["session_id"]
    owner_id = _owner_id(client_a)
    session_dir = _session_dir(app.config["UPLOAD_FOLDER"], owner_id, edit_session_id)

    caplog.set_level(logging.DEBUG, logger="app")
    caplog.clear()

    denied = client_b.get(f"/api/edit/file/{edit_session_id}")
    denied_logs = _caplog_text(caplog)

    assert denied.status_code == 404
    assert session_dir.is_dir()
    assert edit_session_id not in denied_logs
    assert owner_id not in denied_logs
    assert str(app.config["UPLOAD_FOLDER"]) not in denied_logs

    caplog.clear()
    close_response = client_a.post(
        "/api/edit/close",
        json={"session_id": edit_session_id},
        headers={"Accept": "application/json"},
    )
    close_logs = _caplog_text(caplog)

    assert close_response.status_code == 200
    assert not session_dir.exists()
    assert edit_session_id not in close_logs
    assert owner_id not in close_logs
    assert str(app.config["UPLOAD_FOLDER"]) not in close_logs


def test_owner_can_apply_operations_without_exposing_owner_or_paths(app):
    client = app.test_client()
    edit_session_id = _upload_pdf(client)["session_id"]

    organize = client.post(
        "/api/edit/apply/organize",
        json={"session_id": edit_session_id, "order": [1], "rotations": {}},
        headers={"Accept": "application/json"},
    )
    overlay = client.post(
        "/api/edit/overlay",
        json={
            "session_id": edit_session_id,
            "page_width": 72,
            "page_height": 72,
            "ops": [{"type": "whiteout", "pageIndex": 0, "rect": [1, 1, 10, 10]}],
        },
        headers={"Accept": "application/json"},
    )
    overlays = client.post(
        "/api/edit/apply/overlays",
        json={
            "session_id": edit_session_id,
            "page_index": "0",
            "operations": {"whiteouts": [{"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}]},
        },
        headers={"Accept": "application/json"},
    )

    assert organize.status_code == 200
    assert overlay.status_code == 200
    assert overlays.status_code == 200

    for response in (organize, overlay, overlays):
        payload = response.get_json()
        assert payload["download_url"] == f"/api/edit/download/{edit_session_id}"
        assert payload["preview_refresh"] == f"/api/edit/file/{edit_session_id}"
        assert "output_owner_id" not in payload
        assert str(app.config["UPLOAD_FOLDER"]) not in str(payload)


def test_different_flask_session_cannot_use_leaked_edit_session_id(app):
    client_a = app.test_client()
    client_b = app.test_client()
    edit_session_id = _upload_pdf(client_a)["session_id"]
    owner_id = _owner_id(client_a)
    session_dir = _session_dir(app.config["UPLOAD_FOLDER"], owner_id, edit_session_id)

    requests = [
        client_b.get(f"/api/edit/file/{edit_session_id}"),
        client_b.get(f"/api/edit/download/{edit_session_id}"),
        client_b.get(f"/api/edit/page-image/{edit_session_id}/1?scale=0.5"),
        client_b.post(
            "/api/edit/apply/organize",
            json={"session_id": edit_session_id, "order": [1], "rotations": {}},
            headers={"Accept": "application/json"},
        ),
        client_b.post(
            "/api/edit/overlay",
            json={
                "session_id": edit_session_id,
                "page_width": 72,
                "page_height": 72,
                "ops": [{"type": "whiteout", "pageIndex": 0, "rect": [1, 1, 10, 10]}],
            },
            headers={"Accept": "application/json"},
        ),
        client_b.post(
            "/api/edit/apply/overlays",
            json={
                "session_id": edit_session_id,
                "page_index": "0",
                "operations": {"whiteouts": [{"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}]},
            },
            headers={"Accept": "application/json"},
        ),
        client_b.post(
            "/api/edit/overlay-image/upload",
            data={"session_id": edit_session_id, "image": (io.BytesIO(PNG_1X1), "a.png")},
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        ),
        client_b.post(
            "/api/edit/close",
            json={"session_id": edit_session_id},
            headers={"Accept": "application/json"},
        ),
    ]

    assert all(response.status_code == 404 for response in requests)
    assert session_dir.is_dir()
    assert client_a.get(f"/api/edit/file/{edit_session_id}").status_code == 200


def test_owner_value_must_match_even_when_session_id_exists(app):
    client_a = app.test_client()
    client_b = app.test_client()
    edit_session_id = _upload_pdf(client_a)["session_id"]
    _set_owner(client_b, OWNER_B)

    assert client_b.get(f"/api/edit/file/{edit_session_id}").status_code == 404


@pytest.mark.parametrize(
    "bad_session_id",
    [
        "short",
        "g" * 32,
        "a" * 33,
        "..",
        "%2e%2e",
        "a" * 16 + "/" + "b" * 16,
        "a" * 16 + "%2f" + "b" * 16,
    ],
)
def test_invalid_edit_session_ids_return_404(app, bad_session_id):
    client = app.test_client()
    _upload_pdf(client)

    response = client.get(f"/api/edit/file/{bad_session_id}")

    assert response.status_code == 404


def test_owner_can_upload_overlay_image_but_other_session_cannot(app):
    client_a = app.test_client()
    client_b = app.test_client()
    edit_session_id = _upload_pdf(client_a)["session_id"]
    owner_id = _owner_id(client_a)

    owner_response = client_a.post(
        "/api/edit/overlay-image/upload",
        data={"session_id": edit_session_id, "image": (io.BytesIO(PNG_1X1), "marca.png")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    other_response = client_b.post(
        "/api/edit/overlay-image/upload",
        data={"session_id": edit_session_id, "image": (io.BytesIO(PNG_1X1), "marca.png")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert owner_response.status_code == 200
    payload = owner_response.get_json()
    assert payload["ok"] is True
    assert re.fullmatch(r"img_[0-9a-f]{12}\.(?:png|jpg)", payload["image_id"])
    assert _session_dir(app.config["UPLOAD_FOLDER"], owner_id, edit_session_id).joinpath(
        "overlays", payload["image_id"]
    ).is_file()
    assert other_response.status_code == 404


@pytest.mark.parametrize("endpoint", ["/api/edit/overlay", "/api/edit/apply/overlays"])
def test_overlay_image_id_traversal_returns_404_without_writing_outside(app, endpoint):
    client = app.test_client()
    edit_session_id = _upload_pdf(client)["session_id"]
    outside = Path(app.config["UPLOAD_FOLDER"]).parent / "outside.png"

    if endpoint.endswith("/overlay"):
        response = client.post(
            endpoint,
            json={
                "session_id": edit_session_id,
                "page_width": 72,
                "page_height": 72,
                "ops": [
                    {
                        "type": "image",
                        "image_id": "../outside.png",
                        "pageIndex": 0,
                        "rect": [1, 1, 10, 10],
                    }
                ],
            },
            headers={"Accept": "application/json"},
        )
    else:
        response = client.post(
            endpoint,
            json={
                "session_id": edit_session_id,
                "page_index": "0",
                "operations": {
                    "images": [
                        {"image_id": "../outside.png", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2}
                    ]
                },
            },
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 404
    assert not outside.exists()


def test_symlinked_edit_session_resolving_outside_upload_folder_returns_404(app, tmp_path):
    if os.name == "nt":
        pytest.skip("Criacao de symlink em Windows depende de privilegio do ambiente.")

    client = app.test_client()
    _set_owner(client, OWNER_A)
    edit_session_id = "1" * 32
    owner_root = Path(app.config["UPLOAD_FOLDER"], "edit_sessions", OWNER_A)
    outside = tmp_path / "outside-session"
    outside.mkdir()
    (outside / "current.pdf").write_bytes(_pdf_bytes())
    owner_root.mkdir(parents=True)

    try:
        (owner_root / edit_session_id).symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Criacao de symlink indisponivel: {exc}")

    assert client.get(f"/api/edit/file/{edit_session_id}").status_code == 404


def test_edit_upload_still_requires_csrf_by_default(tmp_path):
    csrf_app = create_app()
    csrf_app.config["TESTING"] = True
    csrf_app.config["RATELIMIT_ENABLED"] = False
    csrf_app.config["UPLOAD_FOLDER"] = tmp_path

    response = csrf_app.test_client().post(
        "/api/edit/upload",
        data={"file": (io.BytesIO(_pdf_bytes()), "entrada.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code in {400, 422}
    assert "csrf" in response.get_data(as_text=True).lower()
