import io
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from PyPDF2 import PdfWriter
from werkzeug.exceptions import BadRequest

from app import create_app
from app.utils.security import OUTPUT_OWNER_SESSION_KEY


OWNER_A = "a" * 32
OWNER_B = "b" * 32
PREVIOUS_JOB = "1" * 32
OTHER_JOB = "2" * 32


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _write_structural_output(path: Path, target: str) -> None:
    if target == "pdf":
        path.write_bytes(_pdf_bytes())
        return
    if target == "csv":
        path.write_text("coluna\nvalor\n", encoding="utf-8")
        return
    if target == "docx":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/content-types"></Types>'
                ),
            )
            archive.writestr(
                "word/document.xml",
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body/></w:document>'
                ),
            )
        return

    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active["A1"] = "valor"
    workbook.save(path)
    workbook.close()
    if target == "xlsm":
        rewritten = path.with_suffix(".rewritten.xlsm")
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            rewritten,
            "w",
        ) as destination:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    payload = payload.replace(
                        b"application/vnd.openxmlformats-officedocument."
                        b"spreadsheetml.sheet.main+xml",
                        b"application/vnd.ms-excel.sheet.macroEnabled."
                        b"main+xml",
                    )
                destination.writestr(item, payload)
        rewritten.replace(path)


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=tmp_path,
        CONVERTER_MAX_FILES=10,
    )
    return app


def _uploads(count, *, field="files[]", filename_factory=None):
    filename_factory = filename_factory or (lambda index: f"entrada-{index}.pdf")
    return {
        field: [
            (io.BytesIO(_pdf_bytes()), filename_factory(index))
            for index in range(count)
        ]
    }


def _fake_converter_factory(*, fail_at=None, error_type=RuntimeError, temp_dirs=None):
    calls = {"count": 0}

    def fake_convert(upload_file, target, out_dir):
        index = calls["count"]
        calls["count"] += 1
        item_dir = Path(out_dir)
        if temp_dirs is not None:
            temp_dirs.append(item_dir)
        if fail_at is not None and index == fail_at:
            raise error_type("falha controlada sem dados sensíveis")
        suffix = "pdf" if target == "pdf" else target
        output = item_dir / f"saida-{index}.{suffix}"
        _write_structural_output(output, target)
        return str(output)

    return fake_convert, calls


def _generated_files(upload_folder):
    generated = Path(upload_folder) / "generated"
    if not generated.exists():
        return []
    return sorted(path for path in generated.rglob("*") if path.is_file())


def _generated_job_dirs(upload_folder):
    generated = Path(upload_folder) / "generated"
    if not generated.exists():
        return []
    return sorted(
        path
        for path in generated.glob("*/*")
        if path.is_dir()
    )


def _set_owner(client, owner_id):
    with client.session_transaction() as session:
        session[OUTPUT_OWNER_SESSION_KEY] = owner_id


def _write_previous(upload_folder, owner_id, job_id, name):
    path = Path(upload_folder) / "generated" / owner_id / job_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_pdf_bytes())
    return path


def _rel_from_download_url(download_url):
    path = unquote(urlparse(download_url).path)
    prefix = "/viewer/raw/"
    assert path.startswith(prefix)
    return path[len(prefix):]


def test_zero_files_returns_controlled_422(app):
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data={},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert response.get_json() == {"error": "Nenhum arquivo válido enviado."}


def test_exact_limit_is_processed(app, monkeypatch):
    from app.routes import converter as converter_routes

    app.config["CONVERTER_MAX_FILES"] = 2
    fake_convert, calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["count"] == 2
    assert calls["count"] == 2


@pytest.mark.parametrize("field", ["files[]", "files", "file"])
def test_limit_plus_one_is_rejected_before_conversion_for_every_file_field(
    app, monkeypatch, field
):
    from app.routes import converter as converter_routes

    app.config["CONVERTER_MAX_FILES"] = 2
    fake_convert, calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(3, field=field),
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "Envie no máximo 2 arquivos por vez."
    }
    assert calls["count"] == 0
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


@pytest.mark.parametrize(
    ("endpoint", "extra_form"),
    [
        ("/api/convert/to-pdf", {}),
        ("/api/convert/to-docx", {}),
        ("/api/convert/to-csv", {}),
        ("/api/convert/to-xlsx", {}),
        ("/api/convert/to-xlsm", {}),
        ("/api/convert", {"target": "pdf"}),
        ("/api/convert/merge-a4", {}),
        ("/api/convert/to-pdf-merge", {}),
    ],
)
def test_limit_applies_to_every_converter_endpoint(app, endpoint, extra_form):
    app.config["CONVERTER_MAX_FILES"] = 1
    data = _uploads(2)
    data.update(extra_form)

    response = app.test_client().post(
        endpoint,
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "Envie no máximo 1 arquivo por vez."
    }


@pytest.mark.parametrize("invalid_value", [None, "inválido", 0, -5])
def test_invalid_converter_limit_uses_safe_fallback(
    app, monkeypatch, invalid_value
):
    from app.routes import converter as converter_routes

    monkeypatch.delenv("CONVERTER_MAX_FILES", raising=False)
    app.config["CONVERTER_MAX_FILES"] = invalid_value
    fake_convert, calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(11),
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert response.get_json() == {
        "error": "Envie no máximo 10 arquivos por vez."
    }
    assert calls["count"] == 0


def test_converter_limit_is_loaded_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERTER_MAX_FILES", "4")

    configured_app = create_app()
    configured_app.config["UPLOAD_FOLDER"] = tmp_path

    assert configured_app.config["CONVERTER_MAX_FILES"] == 4


def test_empty_file_item_is_not_counted_toward_limit(app, monkeypatch):
    from app.routes import converter as converter_routes

    app.config["CONVERTER_MAX_FILES"] = 1
    fake_convert, calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    response = app.test_client().post(
        "/api/convert/to-pdf",
        data={
            "files[]": [
                (io.BytesIO(b""), ""),
                (io.BytesIO(_pdf_bytes()), "valido.pdf"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["count"] == 1
    assert calls["count"] == 1


@pytest.mark.parametrize(
    ("fail_at", "error_type", "expected_status"),
    [
        (1, RuntimeError, 503),
        (2, RuntimeError, 503),
        (1, BadRequest, 422),
        (1, ValueError, 500),
    ],
)
def test_failed_batch_publishes_nothing_and_removes_temporary_dirs(
    app, monkeypatch, fail_at, error_type, expected_status
):
    from app.routes import converter as converter_routes

    temp_dirs = []
    fake_convert, _calls = _fake_converter_factory(
        fail_at=fail_at,
        error_type=error_type,
        temp_dirs=temp_dirs,
    )
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(3),
        content_type="multipart/form-data",
    )

    assert response.status_code == expected_status
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []
    assert _generated_job_dirs(app.config["UPLOAD_FOLDER"]) == []
    assert temp_dirs
    assert all(not path.exists() for path in temp_dirs)


def test_failure_publishing_second_result_cleans_only_current_job(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    client = app.test_client()
    _set_owner(client, OWNER_A)
    previous = _write_previous(
        app.config["UPLOAD_FOLDER"], OWNER_A, PREVIOUS_JOB, "anterior.pdf"
    )
    other = _write_previous(
        app.config["UPLOAD_FOLDER"], OWNER_B, OTHER_JOB, "outra-sessao.pdf"
    )

    fake_convert, _calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)
    original_move = converter_routes._move_into_uploads
    move_calls = {"count": 0}

    def fail_second_move(*args, **kwargs):
        move_calls["count"] += 1
        if move_calls["count"] == 2:
            raise RuntimeError("falha de publicação")
        return original_move(*args, **kwargs)

    monkeypatch.setattr(converter_routes, "_move_into_uploads", fail_second_move)

    response = client.post(
        "/api/convert/to-pdf",
        data=_uploads(3),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert previous.exists()
    assert other.exists()
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == [previous, other]
    assert _generated_job_dirs(app.config["UPLOAD_FOLDER"]) == [
        previous.parent,
        other.parent,
    ]


def test_file_metadata_failure_removes_every_output_from_current_job(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    fake_convert, _calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)

    def fail_metadata(_path):
        raise RuntimeError("falha ao montar metadados")

    monkeypatch.setattr(
        converter_routes,
        "_file_info_for_response",
        fail_metadata,
    )

    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []
    assert _generated_job_dirs(app.config["UPLOAD_FOLDER"]) == []


def test_success_preserves_order_unique_names_and_one_atomic_job(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    fake_convert, _calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)
    client = app.test_client()

    response = client.post(
        "/api/convert/to-pdf",
        data=_uploads(
            2,
            filename_factory=lambda _index: "duplicado.pdf",
        ),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    assert [item["name"] for item in payload["files"]] == [
        "duplicado.pdf",
        "duplicado (1).pdf",
    ]

    rel_paths = [
        _rel_from_download_url(item["download_url"])
        for item in payload["files"]
    ]
    assert len({path.split("/")[2] for path in rel_paths}) == 1
    assert all(client.get(item["download_url"]).status_code == 200 for item in payload["files"])


def test_generic_endpoint_has_atomic_cleanup(app, monkeypatch):
    from app.routes import converter as converter_routes

    fake_convert, _calls = _fake_converter_factory(fail_at=1)
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)
    data = _uploads(2)
    data["target"] = "pdf"

    response = app.test_client().post(
        "/api/convert",
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_merge_cleans_published_output_when_metadata_fails(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    def fake_merge(uploads, workdir, normalize, norm_page_size):
        assert len(uploads) == 2
        output = Path(workdir) / "unido.pdf"
        output.write_bytes(_pdf_bytes())
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_many_uploads_to_single_pdf",
        fake_merge,
    )
    monkeypatch.setattr(
        converter_routes,
        "_file_info_for_response",
        lambda _path: (_ for _ in ()).throw(RuntimeError("falha de metadados")),
    )

    response = app.test_client().post(
        "/api/convert/merge-a4",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []
    assert _generated_job_dirs(app.config["UPLOAD_FOLDER"]) == []


def test_merge_cleans_published_output_when_json_response_fails(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    def fake_merge(uploads, workdir, normalize, norm_page_size):
        output = Path(workdir) / "unido.pdf"
        output.write_bytes(_pdf_bytes())
        return str(output)

    original_jsonify = converter_routes.jsonify

    def fail_success_json(*args, **kwargs):
        payload = args[0] if args else kwargs
        if isinstance(payload, dict) and "files" in payload:
            raise ValueError("falha ao montar resposta")
        return original_jsonify(*args, **kwargs)

    monkeypatch.setattr(
        converter_routes,
        "convert_many_uploads_to_single_pdf",
        fake_merge,
    )
    monkeypatch.setattr(converter_routes, "jsonify", fail_success_json)

    response = app.test_client().post(
        "/api/convert/merge-a4",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []
    assert _generated_job_dirs(app.config["UPLOAD_FOLDER"]) == []


def test_merge_alias_processes_once_and_preserves_contract(app, monkeypatch):
    from app.routes import converter as converter_routes

    calls = {"count": 0}

    def fake_merge(uploads, workdir, normalize, norm_page_size):
        calls["count"] += 1
        output = Path(workdir) / "unido.pdf"
        output.write_bytes(_pdf_bytes())
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_many_uploads_to_single_pdf",
        fake_merge,
    )

    response = app.test_client().post(
        "/api/convert/to-pdf-merge",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert calls["count"] == 1
    payload = response.get_json()
    assert payload["count"] == 1
    assert len(payload["files"]) == 1
    assert set(payload["files"][0]) == {"name", "size", "download_url"}


def test_template_exposes_effective_backend_limit(app):
    app.config["CONVERTER_MAX_FILES"] = 7

    response = app.test_client().get("/converter/")

    assert response.status_code == 200
    assert b'data-max-files="7"' in response.data


def test_converter_pages_smoke(app):
    client = app.test_client()

    assert client.get("/converter/select").status_code == 200
    assert client.get("/converter/").status_code == 200


@pytest.mark.parametrize(
    ("endpoint", "extra_form"),
    [
        ("/api/convert/to-pdf", {}),
        ("/api/convert/to-xlsx", {}),
        ("/api/convert", {"target": "pdf"}),
    ],
)
def test_converter_post_smoke_with_mocked_conversion(
    app, monkeypatch, endpoint, extra_form
):
    from app.routes import converter as converter_routes

    fake_convert, calls = _fake_converter_factory()
    monkeypatch.setattr(converter_routes, "convert_upload_to_target", fake_convert)
    data = _uploads(1)
    data.update(extra_form)

    response = app.test_client().post(
        endpoint,
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["count"] == 1
    assert calls["count"] == 1


def test_merge_post_smoke_with_mocked_conversion(app, monkeypatch):
    from app.routes import converter as converter_routes

    def fake_merge(uploads, workdir, normalize, norm_page_size):
        output = Path(workdir) / "unido.pdf"
        output.write_bytes(_pdf_bytes())
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_many_uploads_to_single_pdf",
        fake_merge,
    )

    response = app.test_client().post(
        "/api/convert/merge-a4",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["count"] == 1


def test_operational_template_loads_only_the_active_converter_controller(app):
    response = app.test_client().get("/converter/")

    assert response.status_code == 200
    assert b"js/convert.js" in response.data
    assert b"js/converter-page.js" not in response.data
