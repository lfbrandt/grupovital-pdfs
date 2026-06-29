from __future__ import annotations

import io
import json
import logging
import shutil
import time
import zipfile
from pathlib import Path

import pikepdf
import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from app.services import compress_service
from app.utils.pdf_utils import (
    PDF_PRESERVATION_WARNING,
    PDF_RESIZE_IGNORED_WARNING,
    PDF_SIGNATURE_REWRITE_WARNING,
    pdf_requires_content_preservation,
    register_response_file_cleanup,
    write_preserving_pdf_subset,
)
from tests.pdf_fixture_factory import (
    FIELD_PAGE_1,
    FIELD_PAGE_2,
    FIELD_SIG,
    PAGE1_TEXT,
    PAGE2_TEXT,
    VALUE_PAGE_1,
    VALUE_PAGE_2,
    extract_text,
    inspect_pdf,
    make_annotation_only_pdf,
    make_plain_pdf,
    make_synthetic_pdf,
    make_text_field_only_pdf,
    signature_region_stats,
)


@pytest.fixture
def app(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["RATELIMIT_ENABLED"] = False
    app.config["UPLOAD_FOLDER"] = tmp_path
    return app


def _as_filestorage(path: Path) -> FileStorage:
    return FileStorage(stream=io.BytesIO(path.read_bytes()), filename="gv-fixture.pdf")


def _patch_thumbnail(monkeypatch):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "_generate_page_thumbnail",
        lambda _pdf_path, page_index: f"data:image/svg+xml;base64,page-{page_index + 1}",
    )
    return compress_routes


def _analyze(client, path: Path, monkeypatch) -> str:
    _patch_thumbnail(monkeypatch)
    response = client.post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(path.read_bytes()), "gv-fixture.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["analyse_id"]


def _write_response_pdf(response, path: Path) -> Path:
    assert response.status_code == 200, response.get_data(as_text=True)
    path.write_bytes(response.data)
    response.close()
    return path


def _consume_response_body(response) -> bytes:
    return b"".join(response.response)


def _assert_no_paths(upload_folder: Path, *patterns: str) -> None:
    leftovers = []
    for pattern in patterns:
        leftovers.extend(upload_folder.glob(pattern))
    assert leftovers == []


def _make_empty_acroform_pdf(path: Path) -> Path:
    target = make_plain_pdf(path)
    with pikepdf.open(target, allow_overwriting_input=True) as pdf:
        pdf.Root["/AcroForm"] = pikepdf.Dictionary({"/Fields": pikepdf.Array()})
        pdf.save(target)
    return target


def _make_parent_signature_pdf(path: Path) -> Path:
    target = make_plain_pdf(path)
    with pikepdf.open(target, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        parent = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/FT": pikepdf.Name("/Sig"),
                    "/T": pikepdf.String("parent_signature"),
                    "/Kids": pikepdf.Array(),
                }
            )
        )
        widget = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Widget"),
                    "/Rect": pikepdf.Array([72, 420, 252, 460]),
                    "/F": 4,
                    "/P": page.obj,
                    "/Parent": parent,
                }
            )
        )
        parent["/Kids"] = pikepdf.Array([widget])
        page["/Annots"] = pikepdf.Array([widget])
        pdf.Root["/AcroForm"] = pikepdf.Dictionary(
            {"/Fields": pikepdf.Array([parent]), "/SigFlags": 3}
        )
        pdf.save(target)
    return target


def _dangerous_removed(info: dict) -> None:
    assert info["has_javascript"] is False
    assert info["embedded_files"] == []


def _warnings_are_non_sensitive(warning_header: str, source_path: Path) -> None:
    assert PDF_PRESERVATION_WARNING in warning_header
    assert FIELD_PAGE_1 not in warning_header
    assert FIELD_PAGE_2 not in warning_header
    assert VALUE_PAGE_1 not in warning_header
    assert VALUE_PAGE_2 not in warning_header
    assert str(source_path) not in warning_header
    assert "continua valida" not in warning_header.lower()


def _assert_page1_preserved(output: Path) -> None:
    info = inspect_pdf(output)
    assert info["page_count"] == 1
    assert PAGE1_TEXT in extract_text(output)
    assert PAGE2_TEXT not in extract_text(output)
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert FIELD_PAGE_2 not in info["fields"]
    assert info["fields"][FIELD_SIG]["type"] == "/Sig"
    assert info["page_annots"][0]["widget_count"] == 2
    assert len(info["page_annots"][0]["non_widget_annots"]) == 1
    assert info["signature_widgets"][0]["has_ap_n"] is True
    _dangerous_removed(info)


def _assert_widget_pointers(output: Path) -> None:
    with pikepdf.open(output, suppress_warnings=True) as pdf:
        page = pdf.pages[0]
        for annot in page.get("/Annots", []):
            if str(annot.get("/Subtype", "")) == "/Widget":
                assert annot.get("/P") == page.obj


def test_preservation_detection_is_structured_and_non_sensitive(tmp_path):
    interactive = make_synthetic_pdf(tmp_path / "interactive.pdf")
    plain = make_plain_pdf(tmp_path / "plain.pdf")
    annotation_only = make_annotation_only_pdf(tmp_path / "annotation.pdf")
    text_field_only = make_text_field_only_pdf(tmp_path / "textfield.pdf")
    empty_acroform = _make_empty_acroform_pdf(tmp_path / "empty_acroform.pdf")
    parent_signature = _make_parent_signature_pdf(tmp_path / "parent_signature.pdf")

    detected = pdf_requires_content_preservation(str(interactive))
    allowed_keys = {
        "requires_preservation",
        "has_acroform",
        "has_filled_fields",
        "has_widgets",
        "has_signature_fields",
        "has_annotations",
        "has_annotation_appearances",
        "has_need_appearances",
        "has_sigflags",
    }
    assert set(detected) == allowed_keys
    assert all(type(value) is bool for value in detected.values())
    assert detected["requires_preservation"] is True
    assert detected["has_acroform"] is True
    assert detected["has_filled_fields"] is True
    assert detected["has_widgets"] is True
    assert detected["has_signature_fields"] is True
    assert detected["has_annotations"] is True
    assert detected["has_annotation_appearances"] is True
    assert detected["has_sigflags"] is True

    assert pdf_requires_content_preservation(str(plain))["requires_preservation"] is False
    assert pdf_requires_content_preservation(str(empty_acroform))["requires_preservation"] is False

    annotation_detected = pdf_requires_content_preservation(str(annotation_only))
    assert annotation_detected["requires_preservation"] is True
    assert annotation_detected["has_annotation_appearances"] is False

    text_detected = pdf_requires_content_preservation(str(text_field_only))
    assert text_detected["requires_preservation"] is True
    assert text_detected["has_acroform"] is True
    assert text_detected["has_widgets"] is True

    parent_sig_detected = pdf_requires_content_preservation(str(parent_signature))
    assert parent_sig_detected["requires_preservation"] is True
    assert parent_sig_detected["has_signature_fields"] is True

    rendered = repr(detected)
    assert FIELD_PAGE_1 not in rendered
    assert VALUE_PAGE_1 not in rendered
    assert str(interactive) not in rendered


def test_modern_interactive_uses_preserving_path_without_heavy_engine(
    app, tmp_path, monkeypatch
):
    fixture = make_synthetic_pdf(tmp_path / "interactive.pdf")
    compress_routes = _patch_thumbnail(monkeypatch)

    def fail_group(*_args, **_kwargs):
        raise AssertionError("compress group must not run for interactive PDFs")

    monkeypatch.setattr(compress_routes, "comprimir_pdf_com_params", fail_group)

    client = app.test_client()
    analyse_id = _analyze(client, fixture, monkeypatch)
    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "quality": 25, "dpi": 50, "resize_to_a4": True},
                {"page_number": 2, "include": False, "quality": 25, "dpi": 50, "resize_to_a4": True},
            ],
            "rotations": {"1": 90},
        },
        headers={"Accept": "application/pdf"},
    )

    output = _write_response_pdf(response, tmp_path / "modern_preserved_page1.pdf")
    warnings = response.headers.get("X-Compress-Warnings", "")

    _assert_page1_preserved(output)
    _assert_widget_pointers(output)
    with pikepdf.open(output, suppress_warnings=True) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90

    assert response.headers["X-Fallback"] == "preserved_interactive"
    assert float(response.headers["X-Size-Final-KB"]) == pytest.approx(output.stat().st_size / 1024, abs=0.1)
    assert PDF_RESIZE_IGNORED_WARNING in warnings
    assert PDF_SIGNATURE_REWRITE_WARNING in warnings
    _warnings_are_non_sensitive(warnings, fixture)


def test_modern_interactive_without_sig_has_no_signature_warning(
    app, tmp_path, monkeypatch
):
    fixture = make_text_field_only_pdf(tmp_path / "text_field_only.pdf")
    compress_routes = _patch_thumbnail(monkeypatch)
    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("heavy engine called")),
    )

    client = app.test_client()
    analyse_id = _analyze(client, fixture, monkeypatch)
    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "quality": 50, "dpi": 90},
            ],
        },
        headers={"Accept": "application/pdf"},
    )

    _write_response_pdf(response, tmp_path / "modern_text_field_only.pdf")
    warnings = response.headers.get("X-Compress-Warnings", "")

    assert PDF_PRESERVATION_WARNING in warnings
    assert PDF_SIGNATURE_REWRITE_WARNING not in warnings


def test_modern_interactive_preserves_signature_visual_without_rotation(
    app, tmp_path, monkeypatch
):
    fixture = make_synthetic_pdf(tmp_path / "interactive_visual.pdf")
    compress_routes = _patch_thumbnail(monkeypatch)
    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("heavy engine called")),
    )

    client = app.test_client()
    analyse_id = _analyze(client, fixture, monkeypatch)
    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "quality": 80, "dpi": 100},
                {"page_number": 2, "include": True, "quality": 80, "dpi": 100},
            ],
        },
        headers={"Accept": "application/pdf"},
    )

    output = _write_response_pdf(response, tmp_path / "modern_preserved_visual.pdf")
    info = inspect_pdf(output)

    assert info["page_count"] == 2
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2
    assert info["signature_widgets"][0]["field_type"] == "/Sig"
    assert info["signature_widgets"][0]["has_ap_n"] is True
    assert signature_region_stats(output)["non_white_ratio"] > 0.01
    _dangerous_removed(info)


def test_modern_plain_pdf_still_uses_normal_engine(app, tmp_path, monkeypatch):
    plain = make_plain_pdf(tmp_path / "plain.pdf")
    compress_routes = _patch_thumbnail(monkeypatch)
    received = {}

    def fake_group(input_path, output_path, pages, quality, dpi, resize_to_a4=False, rotations=None):
        received["preservation"] = pdf_requires_content_preservation(input_path)
        received["pages"] = pages
        received["quality"] = quality
        received["dpi"] = dpi
        received["resize_to_a4"] = resize_to_a4
        shutil.copyfile(input_path, output_path)
        return []

    monkeypatch.setattr(compress_routes, "comprimir_pdf_com_params", fake_group)

    client = app.test_client()
    analyse_id = _analyze(client, plain, monkeypatch)
    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "quality": 65, "dpi": 110, "resize_to_a4": False},
                {"page_number": 2, "include": True, "quality": 65, "dpi": 110, "resize_to_a4": False},
            ],
        },
        headers={"Accept": "application/pdf"},
    )

    _write_response_pdf(response, tmp_path / "modern_plain.pdf")
    assert received["preservation"]["requires_preservation"] is False
    assert received["pages"] == [1, 2]
    assert received["quality"] == 65
    assert received["dpi"] == 110
    assert received["resize_to_a4"] is False
    assert response.headers.get("X-Compress-Warnings") is None


def test_modern_session_path_outside_upload_folder_is_rejected(app, tmp_path):
    outside = tmp_path.parent / "outside_session_source.pdf"
    make_plain_pdf(outside)
    session_id = "tamperedoutside"
    session_file = tmp_path / f".session_{session_id}"
    session_file.write_text(
        json.dumps({"path": str(outside), "ts": time.time()}),
        encoding="utf-8",
    )

    response = app.test_client().post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": session_id,
            "page_settings": [{"page_number": 1, "include": True}],
        },
        headers={"Accept": "application/pdf"},
    )

    assert response.status_code == 404
    assert outside.exists()
    assert not session_file.exists()


def test_modern_processing_error_cleans_session_source(app, tmp_path, monkeypatch, caplog):
    plain = make_plain_pdf(tmp_path / "plain_cleanup_error.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, plain, monkeypatch)

    session_file = tmp_path / f".session_{analyse_id}"
    source_path = Path(json.loads(session_file.read_text(encoding="utf-8"))["path"])
    assert source_path.exists()

    from app.routes import compress as compress_routes

    sensitive_message = (
        f"inspection failed at {source_path} {analyse_id} "
        f"{FIELD_PAGE_1} {VALUE_PAGE_1}"
    )

    def fail_inspection(_path):
        raise RuntimeError(sensitive_message)

    monkeypatch.setattr(
        compress_routes,
        "pdf_requires_content_preservation",
        fail_inspection,
    )

    caplog.set_level(logging.ERROR, logger="app")
    caplog.clear()
    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": [{"page_number": 1, "include": True}],
        },
        headers={"Accept": "application/pdf"},
    )

    assert response.status_code == 500
    assert not session_file.exists()
    assert not source_path.exists()

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "[process-with-settings]" in log_text
    assert "RuntimeError" in log_text
    for forbidden in (
        str(Path.cwd()),
        str(app.config["UPLOAD_FOLDER"]),
        str(source_path),
        analyse_id,
        FIELD_PAGE_1,
        VALUE_PAGE_1,
        "inspection failed",
        "gv-fixture.pdf",
    ):
        assert forbidden not in log_text
    assert "Traceback" not in caplog.text
    assert 'File "' not in caplog.text


def test_response_file_cleanup_removes_only_upload_owned_paths(app, tmp_path, caplog):
    upload_file = tmp_path / "inside.tmp"
    upload_file.write_bytes(b"inside")
    outside_file = tmp_path.parent / f"{tmp_path.name}_outside.tmp"
    outside_file.write_bytes(b"outside")

    caplog.set_level(logging.DEBUG)
    response = app.response_class(b"ok")
    register_response_file_cleanup(
        response,
        (str(upload_file), str(outside_file)),
        str(tmp_path),
    )
    response.close()

    assert not upload_file.exists()
    assert outside_file.exists()
    assert str(outside_file) not in caplog.text
    outside_file.unlink()


def test_compress_interactive_cleanup_removes_preserved_after_response_close(
    app, tmp_path, monkeypatch
):
    fixture = make_synthetic_pdf(tmp_path / "cleanup_interactive.pdf")
    from app.routes import compress as compress_routes

    client = app.test_client()
    analyse_id = _analyze(client, fixture, monkeypatch)
    with app.test_request_context(
        "/api/compress/process-with-settings",
        method="POST",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "quality": 25, "dpi": 50},
            ],
        },
        headers={"Accept": "application/pdf"},
    ):
        response = compress_routes.process_with_settings()

    output = tmp_path / "received_interactive.pdf"
    assert response.status_code == 200, response.get_data(as_text=True)
    output.write_bytes(_consume_response_body(response))
    assert inspect_pdf(output)["page_count"] == 1
    assert list(tmp_path.glob("preserved_*.pdf"))
    response.close()
    _assert_no_paths(tmp_path, "preserved_*.pdf")


def test_compress_plain_cleanup_removes_group_output_after_response_close(
    app, tmp_path, monkeypatch
):
    plain = make_plain_pdf(tmp_path / "cleanup_plain.pdf")
    compress_routes = _patch_thumbnail(monkeypatch)

    def fake_group(input_path, output_path, pages, quality, dpi, resize_to_a4=False, rotations=None):
        shutil.copyfile(input_path, output_path)
        return []

    monkeypatch.setattr(compress_routes, "comprimir_pdf_com_params", fake_group)

    client = app.test_client()
    analyse_id = _analyze(client, plain, monkeypatch)
    with app.test_request_context(
        "/api/compress/process-with-settings",
        method="POST",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "quality": 65, "dpi": 110},
                {"page_number": 2, "include": True, "quality": 65, "dpi": 110},
            ],
        },
        headers={"Accept": "application/pdf"},
    ):
        response = compress_routes.process_with_settings()

    output = tmp_path / "received_plain.pdf"
    assert response.status_code == 200, response.get_data(as_text=True)
    output.write_bytes(_consume_response_body(response))
    assert inspect_pdf(output)["page_count"] == 2
    assert list(tmp_path.glob("group_*.pdf"))
    response.close()
    _assert_no_paths(tmp_path, "group_*.pdf", "merged_*.pdf")


def test_split_pdf_cleanup_removes_sent_pdf_after_response_close(app, tmp_path):
    fixture = make_synthetic_pdf(tmp_path / "split_cleanup.pdf")
    from app.routes import split as split_routes

    with app.test_request_context(
        "/api/split",
        method="POST",
        data={
            "file": (io.BytesIO(fixture.read_bytes()), "gv-fixture.pdf"),
            "pages": json.dumps([1]),
        },
        content_type="multipart/form-data",
        headers={"Accept": "application/pdf"},
    ):
        response = split_routes.split()

    output = tmp_path / "received_split.pdf"
    assert response.status_code == 200, response.get_data(as_text=True)
    output.write_bytes(_consume_response_body(response))
    assert inspect_pdf(output)["page_count"] == 1
    assert list(tmp_path.glob("selecionadas_*.pdf"))
    response.close()
    _assert_no_paths(tmp_path, "selecionadas_*.pdf")


def test_split_zip_cleanup_removes_zip_and_internal_pdfs_after_response_close(
    app, tmp_path
):
    fixture = make_synthetic_pdf(tmp_path / "split_zip_cleanup.pdf")
    from app.routes import split as split_routes

    with app.test_request_context(
        "/api/split",
        method="POST",
        data={"file": (io.BytesIO(fixture.read_bytes()), "gv-fixture.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/zip"},
    ):
        response = split_routes.split()

    assert response.status_code == 200, response.get_data(as_text=True)
    with zipfile.ZipFile(io.BytesIO(_consume_response_body(response))) as zipf:
        assert len(zipf.namelist()) == 2
        for name in zipf.namelist():
            output = tmp_path / f"received_{Path(name).stem}.pdf"
            output.write_bytes(zipf.read(name))
            assert inspect_pdf(output)["page_count"] == 1

    assert list(tmp_path.glob("*.zip"))
    _assert_no_paths(tmp_path, "pagina_*.pdf")
    response.close()
    _assert_no_paths(tmp_path, "*.zip", "pagina_*.pdf")


def test_legacy_interactive_skips_qpdf_and_ghostscript(app, tmp_path, monkeypatch):
    fixture = make_synthetic_pdf(tmp_path / "legacy_interactive.pdf")

    monkeypatch.setattr(
        compress_service,
        "_qpdf_flatten",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("qpdf called")),
    )
    monkeypatch.setattr(
        compress_service,
        "_run_ghostscript",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ghostscript called")),
    )

    with app.app_context():
        output, warnings = compress_service.comprimir_pdf(_as_filestorage(fixture))
    output = Path(output)
    info = inspect_pdf(output)

    assert PDF_PRESERVATION_WARNING in warnings
    assert PDF_SIGNATURE_REWRITE_WARNING in warnings
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2
    assert info["signature_widgets"][0]["has_ap_n"] is True
    assert signature_region_stats(output)["non_white_ratio"] > 0.01
    _dangerous_removed(info)


def test_legacy_plain_pdf_still_uses_qpdf_and_ghostscript(app, tmp_path, monkeypatch):
    plain = make_plain_pdf(tmp_path / "legacy_plain.pdf")
    called = {}

    def fake_qpdf(src, dst):
        called["qpdf_preservation"] = pdf_requires_content_preservation(src)
        shutil.copyfile(src, dst)

    def fake_gs(input_pdf, output_pdf, quality, dpi):
        called["gs"] = {"quality": quality, "dpi": dpi}
        shutil.copyfile(input_pdf, output_pdf)

    monkeypatch.setattr(compress_service, "_qpdf_flatten", fake_qpdf)
    monkeypatch.setattr(compress_service, "_run_ghostscript", fake_gs)

    with app.app_context():
        output, warnings = compress_service.comprimir_pdf(_as_filestorage(plain), profile="equilibrio")

    assert Path(output).exists()
    assert warnings == []
    assert called["qpdf_preservation"]["requires_preservation"] is False
    assert called["gs"] == {"quality": 72, "dpi": 120}


def test_legacy_inspection_failure_is_controlled_and_does_not_use_heavy_path(
    app, tmp_path, monkeypatch
):
    plain = make_plain_pdf(tmp_path / "inspection_failure.pdf")

    monkeypatch.setattr(
        compress_service,
        "pdf_requires_content_preservation",
        lambda _path: (_ for _ in ()).throw(RuntimeError("inspection boom")),
    )
    monkeypatch.setattr(
        compress_service,
        "_qpdf_flatten",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("qpdf called")),
    )
    monkeypatch.setattr(
        compress_service,
        "_run_ghostscript",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ghostscript called")),
    )

    with app.app_context(), pytest.raises(RuntimeError, match="preservation_inspection_failed"):
        compress_service.comprimir_pdf(_as_filestorage(plain))


def test_preserving_subset_rejects_empty_selection(tmp_path):
    plain = make_plain_pdf(tmp_path / "plain_for_empty_selection.pdf")

    with pytest.raises(ValueError, match="Nenhuma pagina valida"):
        write_preserving_pdf_subset(str(plain), str(tmp_path / "out.pdf"), pages=[])
