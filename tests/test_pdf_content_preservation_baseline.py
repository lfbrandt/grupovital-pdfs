from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pikepdf
import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from app.services.sanitize_service import sanitize_pdf, sanitize_pdf_preserving_content
from app.services import compress_service, split_service
from app.utils.pdf_utils import PDF_PRESERVATION_WARNING, PDF_SIGNATURE_REWRITE_WARNING
from tests.pdf_fixture_factory import (
    EMBEDDED_CONTENT,
    EMBEDDED_NAME,
    FIELD_PARENT_KIDS,
    FIELD_PAGE_1,
    FIELD_PAGE_2,
    FIELD_SIG,
    PAGE1_TEXT,
    PAGE2_TEXT,
    PLAIN_PAGE1_TEXT,
    PLAIN_PAGE2_TEXT,
    VALUE_PARENT_KIDS,
    VALUE_PAGE_1,
    VALUE_PAGE_2,
    extract_text,
    inspect_pdf,
    make_parent_kids_pdf,
    make_plain_pdf,
    make_synthetic_pdf,
    signature_region_stats,
)


# GV-P1-001 baseline only:
# this synthetic fixture does not create or validate a real cryptographic
# signature. It validates the /Sig widget, its /AP appearance, and visual
# rendering of the known signature rectangle.


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
    return make_synthetic_pdf(tmp_path / "gv_p1_001_fixture.pdf")


def _as_filestorage(path: Path) -> FileStorage:
    return FileStorage(stream=io.BytesIO(path.read_bytes()), filename="gv-fixture.pdf")


def _sanitize_to(path: Path, tmp_path: Path, suffix: str, **kwargs) -> Path:
    output = tmp_path / f"sanitized_{suffix}.pdf"
    sanitize_pdf(str(path), str(output), **kwargs)
    return output


def _sanitize_preserving_to(path: Path, tmp_path: Path, suffix: str) -> Path:
    output = tmp_path / f"sanitized_{suffix}.pdf"
    sanitize_pdf_preserving_content(str(path), str(output))
    return output


def _split_service_output(app, fixture_pdf: Path, pages=None) -> Path:
    with app.app_context():
        outputs = split_service.dividir_pdf(_as_filestorage(fixture_pdf), pages=pages)
    assert len(outputs) == 1
    return Path(outputs[0])


def _split_service_outputs(app, fixture_pdf: Path, pages=None) -> list[Path]:
    with app.app_context():
        outputs = split_service.dividir_pdf(_as_filestorage(fixture_pdf), pages=pages)
    return [Path(output) for output in outputs]


def _assert_original_fixture(info: dict) -> None:
    _assert_two_page_legitimate_content(info)
    assert info["calculation_order"] == [FIELD_PAGE_1, FIELD_PAGE_2]
    assert info["has_javascript"] is True
    assert info["embedded_files"] == [{"name": EMBEDDED_NAME, "content": EMBEDDED_CONTENT}]


def _assert_two_page_legitimate_content(info: dict) -> None:
    assert info["page_count"] == 2
    assert info["has_acroform"] is True
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2
    assert info["fields"][FIELD_SIG]["type"] == "/Sig"
    assert info["page_annots"][0]["widget_count"] == 2
    assert info["page_annots"][1]["widget_count"] == 1
    assert len(info["page_annots"][0]["non_widget_annots"]) == 1
    assert len(info["page_annots"][1]["non_widget_annots"]) == 1
    assert len(info["signature_widgets"]) == 1
    assert info["signature_widgets"][0]["field_name"] == FIELD_SIG
    assert info["signature_widgets"][0]["field_type"] == "/Sig"
    assert info["signature_widgets"][0]["has_ap_n"] is True


def _assert_dangerous_content_removed(info: dict) -> None:
    assert info["has_javascript"] is False
    assert info["embedded_files"] == []


def _assert_page1_legitimate_content(info: dict, path: Path) -> None:
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert FIELD_PAGE_2 not in info["fields"]
    assert len(info["page_annots"][0]["non_widget_annots"]) == 1
    _assert_page1_signature_widget_appearance(info, path)


def _assert_page1_signature_widget_appearance(info: dict, path: Path) -> None:
    assert info["signature_widgets"][0]["field_name"] == FIELD_SIG
    assert info["signature_widgets"][0]["field_type"] == "/Sig"
    assert info["signature_widgets"][0]["has_ap_n"] is True
    assert signature_region_stats(path)["non_white_ratio"] > 0.01


def _object_key(obj):
    objgen = getattr(obj, "objgen", None)
    if objgen and objgen != (0, 0):
        return objgen
    return ("mem", id(obj))


def _walk_field_tree(field):
    yield field
    for kid in field.get("/Kids", []):
        yield from _walk_field_tree(kid)


def _assert_form_structure_valid(path: Path, expected_fields: set[str] | None = None) -> None:
    with pikepdf.open(path, suppress_warnings=True) as pdf:
        page_keys = {_object_key(page.obj) for page in pdf.pages}
        acroform = pdf.Root.get("/AcroForm")
        if expected_fields is None:
            assert acroform is None or "/Fields" not in acroform
            return

        assert acroform is not None
        assert "/Fields" in acroform
        fields = list(acroform["/Fields"])
        assert fields
        root_field_keys = {_object_key(field) for field in fields}

        field_tree_keys = set()
        field_names = set()
        for field in fields:
            assert "/Parent" not in field
            for node in _walk_field_tree(field):
                field_tree_keys.add(_object_key(node))
                if "/T" in node:
                    field_names.add(str(node["/T"]))
                for bad_key in ("/A", "/AA", "/JS", "/JavaScript", "/OpenAction", "/XFA"):
                    assert bad_key not in node
                for kid in node.get("/Kids", []):
                    assert kid.get("/Parent") == node

        assert field_names == expected_fields

        if "/CO" in acroform:
            for ordered_field in acroform["/CO"]:
                assert _object_key(ordered_field) in root_field_keys
                for bad_key in ("/A", "/AA", "/JS", "/JavaScript", "/OpenAction", "/XFA"):
                    assert bad_key not in ordered_field

        widget_locations = {}
        for page in pdf.pages:
            page_key = _object_key(page.obj)
            for annot in page.get("/Annots", []):
                if str(annot.get("/Subtype", "")) != "/Widget":
                    continue
                widget_key = _object_key(annot)
                widget_locations.setdefault(widget_key, set()).add(page_key)
                assert annot.get("/P") == page.obj
                assert page_key in page_keys
                parent = annot.get("/Parent")
                if parent is not None:
                    assert _object_key(parent) in field_tree_keys
                    assert annot in list(parent.get("/Kids", []))
                else:
                    assert widget_key in field_tree_keys
                for bad_key in ("/A", "/AA", "/JS", "/JavaScript", "/OpenAction", "/XFA"):
                    assert bad_key not in annot

        assert widget_locations
        assert all(len(locations) == 1 for locations in widget_locations.values())


def _write_response_pdf(response, path: Path) -> Path:
    assert response.status_code == 200, response.get_data(as_text=True)
    path.write_bytes(response.data)
    return path


def test_fixture_original_has_expected_structure_and_visual_signature(fixture_pdf):
    info = inspect_pdf(fixture_pdf)
    _assert_original_fixture(info)
    assert PAGE1_TEXT in extract_text(fixture_pdf)
    assert PAGE2_TEXT in extract_text(fixture_pdf)
    stats = signature_region_stats(fixture_pdf)
    assert stats["non_white_ratio"] > 0.01
    assert stats["variance"] > 1.0


def test_sanitize_defaults_remove_dangerous_content_and_currently_strip_interactive_content(
    fixture_pdf, tmp_path
):
    output = _sanitize_to(fixture_pdf, tmp_path, "defaults")
    info = inspect_pdf(output)

    assert info["page_count"] == 2
    _assert_dangerous_content_removed(info)
    assert info["has_acroform"] is False
    assert info["fields"] == {}
    assert all(page["widget_count"] == 0 for page in info["page_annots"])
    assert all(page["non_widget_annots"] == [] for page in info["page_annots"])
    assert info["signature_widgets"] == []
    assert signature_region_stats(output)["non_white_ratio"] < 0.005


def test_sanitize_preserving_mode_removes_dangerous_content_and_preserves_legitimate_content(
    fixture_pdf, tmp_path
):
    output = _sanitize_to(
        fixture_pdf,
        tmp_path,
        "preserve",
        remove_annotations=False,
        remove_actions=True,
        remove_embedded=True,
        preserve_acroform=True,
    )
    info = inspect_pdf(output)

    _assert_dangerous_content_removed(info)
    _assert_two_page_legitimate_content(info)
    assert signature_region_stats(output)["non_white_ratio"] > 0.01


def test_preserving_helper_removes_dangerous_content_and_preserves_legitimate_content(
    fixture_pdf, tmp_path
):
    output = _sanitize_preserving_to(fixture_pdf, tmp_path, "helper_preserve")
    info = inspect_pdf(output)

    _assert_dangerous_content_removed(info)
    _assert_two_page_legitimate_content(info)
    assert signature_region_stats(output)["non_white_ratio"] > 0.01


@pytest.mark.xfail(
    strict=True,
    reason="GV-P1-001: sanitize defaults remove AcroForm fields and filled values",
)
def test_sanitize_defaults_preserve_filled_text_fields_requirement(fixture_pdf, tmp_path):
    output = _sanitize_to(fixture_pdf, tmp_path, "defaults_preservation")
    info = inspect_pdf(output)

    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2


@pytest.mark.xfail(
    strict=True,
    reason="GV-P1-001: sanitize defaults remove signature widget appearance",
)
def test_sanitize_defaults_preserve_signature_widget_appearance_requirement(fixture_pdf, tmp_path):
    output = _sanitize_to(fixture_pdf, tmp_path, "defaults_signature")
    info = inspect_pdf(output)

    assert info["signature_widgets"][0]["has_ap_n"] is True
    assert signature_region_stats(output)["non_white_ratio"] > 0.01


def test_split_service_selected_pages_keep_text_and_remove_dangerous_content(app, fixture_pdf):
    page1 = _split_service_output(app, fixture_pdf, pages=[1])
    page2 = _split_service_output(app, fixture_pdf, pages=[2])
    both = _split_service_output(app, fixture_pdf, pages=[1, 2])

    page1_info = inspect_pdf(page1)
    page2_info = inspect_pdf(page2)
    both_info = inspect_pdf(both)

    assert page1_info["page_count"] == 1
    assert page2_info["page_count"] == 1
    assert both_info["page_count"] == 2
    assert PAGE1_TEXT in extract_text(page1)
    assert PAGE2_TEXT not in extract_text(page1)
    assert PAGE2_TEXT in extract_text(page2)
    assert PAGE1_TEXT not in extract_text(page2)
    assert PAGE1_TEXT in extract_text(both)
    assert PAGE2_TEXT in extract_text(both)
    for info in (page1_info, page2_info, both_info):
        _assert_dangerous_content_removed(info)


def test_split_api_zip_outputs_per_page_pdfs_without_dangerous_content(app, fixture_pdf, tmp_path):
    client = app.test_client()
    response = client.post(
        "/api/split",
        data={"file": (io.BytesIO(fixture_pdf.read_bytes()), "gv-fixture.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/zip"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    with zipfile.ZipFile(io.BytesIO(response.data)) as zip_file:
        names = sorted(zip_file.namelist())
        assert len(names) == 2
        extracted = []
        for index, name in enumerate(names, start=1):
            out = tmp_path / f"zip_page_{index}.pdf"
            out.write_bytes(zip_file.read(name))
            extracted.append(out)

    assert PAGE1_TEXT in extract_text(extracted[0])
    assert PAGE2_TEXT in extract_text(extracted[1])
    for path in extracted:
        info = inspect_pdf(path)
        assert info["page_count"] == 1
        _assert_dangerous_content_removed(info)


def test_split_service_sanitized_source_uses_preserving_profile(app, fixture_pdf, monkeypatch):
    real_helper = sanitize_pdf_preserving_content
    captured = {}

    def capture_preserved_source(input_path, output_path):
        real_helper(input_path, output_path)
        captured["info"] = inspect_pdf(output_path)
        captured["stats"] = signature_region_stats(output_path)

    monkeypatch.setattr(split_service, "sanitize_pdf_preserving_content", capture_preserved_source)

    page1 = _split_service_output(app, fixture_pdf, pages=[1])

    assert PAGE1_TEXT in extract_text(page1)
    _assert_dangerous_content_removed(captured["info"])
    _assert_two_page_legitimate_content(captured["info"])
    assert captured["stats"]["non_white_ratio"] > 0.01


def test_split_preserves_filled_text_field_requirement(app, fixture_pdf):
    page1 = _split_service_output(app, fixture_pdf, pages=[1])
    info = inspect_pdf(page1)

    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert FIELD_PAGE_2 not in info["fields"]
    _assert_form_structure_valid(page1, {FIELD_PAGE_1, FIELD_SIG})


def test_split_preserves_visual_annotation_requirement(app, fixture_pdf):
    page1 = _split_service_output(app, fixture_pdf, pages=[1])
    info = inspect_pdf(page1)

    assert len(info["page_annots"][0]["non_widget_annots"]) == 1


def test_split_preserves_signature_widget_appearance_requirement(app, fixture_pdf):
    page1 = _split_service_output(app, fixture_pdf, pages=[1])
    info = inspect_pdf(page1)

    _assert_page1_signature_widget_appearance(info, page1)


def test_split_page1_output_has_only_page1_fields_and_signature(app, fixture_pdf):
    page1 = _split_service_output(app, fixture_pdf, pages=[1])
    info = inspect_pdf(page1)

    assert info["page_count"] == 1
    assert PAGE1_TEXT in extract_text(page1)
    assert PAGE2_TEXT not in extract_text(page1)
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert FIELD_PAGE_2 not in info["fields"]
    assert info["page_annots"][0]["widget_count"] == 2
    assert len(info["page_annots"][0]["non_widget_annots"]) == 1
    _assert_page1_signature_widget_appearance(info, page1)
    _assert_dangerous_content_removed(info)
    _assert_form_structure_valid(page1, {FIELD_PAGE_1, FIELD_SIG})


def test_split_page2_output_has_only_page2_fields(app, fixture_pdf):
    page2 = _split_service_output(app, fixture_pdf, pages=[2])
    info = inspect_pdf(page2)

    assert info["page_count"] == 1
    assert PAGE2_TEXT in extract_text(page2)
    assert PAGE1_TEXT not in extract_text(page2)
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2
    assert FIELD_PAGE_1 not in info["fields"]
    assert FIELD_SIG not in info["fields"]
    assert info["signature_widgets"] == []
    assert info["page_annots"][0]["widget_count"] == 1
    assert len(info["page_annots"][0]["non_widget_annots"]) == 1
    _assert_dangerous_content_removed(info)
    _assert_form_structure_valid(page2, {FIELD_PAGE_2})


def test_split_two_pages_original_order_preserves_page_fields(app, fixture_pdf):
    both = _split_service_output(app, fixture_pdf, pages=[1, 2])
    info = inspect_pdf(both)

    _assert_two_page_legitimate_content(info)
    _assert_dangerous_content_removed(info)
    assert info["page_annots"][0]["widgets"][0]["field_name"] == FIELD_PAGE_1
    assert info["page_annots"][0]["widgets"][1]["field_name"] == FIELD_SIG
    assert info["page_annots"][1]["widgets"][0]["field_name"] == FIELD_PAGE_2
    _assert_form_structure_valid(both, {FIELD_PAGE_1, FIELD_PAGE_2, FIELD_SIG})


def test_split_two_pages_inverted_order_keeps_fields_on_new_pages(app, fixture_pdf):
    inverted = _split_service_output(app, fixture_pdf, pages=[2, 1])
    info = inspect_pdf(inverted)

    assert info["page_count"] == 2
    assert PAGE2_TEXT in extract_text(inverted)
    assert PAGE1_TEXT in extract_text(inverted)
    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2
    assert info["page_annots"][0]["widgets"][0]["field_name"] == FIELD_PAGE_2
    assert info["page_annots"][1]["widgets"][0]["field_name"] == FIELD_PAGE_1
    assert info["page_annots"][1]["widgets"][1]["field_name"] == FIELD_SIG
    assert info["signature_widgets"][0]["page_number"] == 2
    assert signature_region_stats(inverted, page_index=1)["non_white_ratio"] > 0.01
    _assert_dangerous_content_removed(info)
    _assert_form_structure_valid(inverted, {FIELD_PAGE_1, FIELD_PAGE_2, FIELD_SIG})


def test_split_filters_calculation_order_to_original_exported_fields(app, fixture_pdf):
    assert inspect_pdf(fixture_pdf)["calculation_order"] == [FIELD_PAGE_1, FIELD_PAGE_2]

    page1 = _split_service_output(app, fixture_pdf, pages=[1])
    page2 = _split_service_output(app, fixture_pdf, pages=[2])
    inverted = _split_service_output(app, fixture_pdf, pages=[2, 1])

    page1_info = inspect_pdf(page1)
    page2_info = inspect_pdf(page2)
    inverted_info = inspect_pdf(inverted)

    assert page1_info["calculation_order"] == [FIELD_PAGE_1]
    assert page2_info["calculation_order"] == [FIELD_PAGE_2]
    assert inverted_info["calculation_order"] == [FIELD_PAGE_1, FIELD_PAGE_2]
    assert FIELD_SIG not in page1_info["calculation_order"]
    assert FIELD_SIG not in inverted_info["calculation_order"]

    _assert_form_structure_valid(page1, {FIELD_PAGE_1, FIELD_SIG})
    _assert_form_structure_valid(page2, {FIELD_PAGE_2})
    _assert_form_structure_valid(inverted, {FIELD_PAGE_1, FIELD_PAGE_2, FIELD_SIG})


def test_split_repeated_page_uses_independent_widgets_and_predictable_field_names(app, fixture_pdf):
    repeated = _split_service_output(app, fixture_pdf, pages=[1, 1])
    info = inspect_pdf(repeated)

    assert info["page_count"] == 2
    assert info["field_names"] == [
        FIELD_SIG,
        f"{FIELD_SIG}__copy_2",
        FIELD_PAGE_1,
        f"{FIELD_PAGE_1}__copy_2",
    ]
    assert info["page_annots"][0]["widgets"][0]["field_name"] == FIELD_PAGE_1
    assert info["page_annots"][1]["widgets"][0]["field_name"] == f"{FIELD_PAGE_1}__copy_2"
    assert info["page_annots"][0]["widgets"][1]["field_name"] == FIELD_SIG
    assert info["page_annots"][1]["widgets"][1]["field_name"] == f"{FIELD_SIG}__copy_2"
    assert signature_region_stats(repeated, page_index=0)["non_white_ratio"] > 0.01
    assert signature_region_stats(repeated, page_index=1)["non_white_ratio"] > 0.01
    _assert_dangerous_content_removed(info)
    _assert_form_structure_valid(
        repeated,
        {FIELD_PAGE_1, f"{FIELD_PAGE_1}__copy_2", FIELD_SIG, f"{FIELD_SIG}__copy_2"},
    )


def test_split_zip_outputs_independent_pdfs_with_page_scoped_fields(app, fixture_pdf, tmp_path):
    client = app.test_client()
    response = client.post(
        "/api/split",
        data={"file": (io.BytesIO(fixture_pdf.read_bytes()), "gv-fixture.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/zip"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)

    with zipfile.ZipFile(io.BytesIO(response.data)) as zip_file:
        names = sorted(zip_file.namelist())
        assert len(names) == 2
        page1 = tmp_path / "zip_scoped_page_1.pdf"
        page2 = tmp_path / "zip_scoped_page_2.pdf"
        page1.write_bytes(zip_file.read(names[0]))
        page2.write_bytes(zip_file.read(names[1]))

    page1_info = inspect_pdf(page1)
    page2_info = inspect_pdf(page2)

    assert set(page1_info["field_names"]) == {FIELD_PAGE_1, FIELD_SIG}
    assert page2_info["field_names"] == [FIELD_PAGE_2]
    _assert_page1_signature_widget_appearance(page1_info, page1)
    assert page2_info["signature_widgets"] == []
    _assert_dangerous_content_removed(page1_info)
    _assert_dangerous_content_removed(page2_info)
    _assert_form_structure_valid(page1, {FIELD_PAGE_1, FIELD_SIG})
    _assert_form_structure_valid(page2, {FIELD_PAGE_2})


def test_split_plain_pdf_does_not_create_artificial_acroform(app, tmp_path):
    plain = make_plain_pdf(tmp_path / "plain.pdf")
    page1 = _split_service_output(app, plain, pages=[1])
    info = inspect_pdf(page1)

    assert info["page_count"] == 1
    assert PLAIN_PAGE1_TEXT in extract_text(page1)
    assert PLAIN_PAGE2_TEXT not in extract_text(page1)
    assert info["has_acroform"] is False
    assert info["fields"] == {}
    _assert_dangerous_content_removed(info)
    _assert_form_structure_valid(page1, None)


def test_split_parent_field_with_kids_keeps_only_exported_child(app, tmp_path):
    parent_pdf = make_parent_kids_pdf(tmp_path / "parent_kids.pdf")
    page1 = _split_service_output(app, parent_pdf, pages=[1])
    page2 = _split_service_output(app, parent_pdf, pages=[2])
    both = _split_service_output(app, parent_pdf, pages=[1, 2])

    page1_info = inspect_pdf(page1)
    page2_info = inspect_pdf(page2)
    both_info = inspect_pdf(both)

    assert page1_info["fields"][FIELD_PARENT_KIDS]["value"] == VALUE_PARENT_KIDS
    assert page2_info["fields"][FIELD_PARENT_KIDS]["value"] == VALUE_PARENT_KIDS
    assert both_info["fields"][FIELD_PARENT_KIDS]["value"] == VALUE_PARENT_KIDS
    assert page1_info["page_annots"][0]["widgets"][0]["field_name"] == FIELD_PARENT_KIDS
    assert page2_info["page_annots"][0]["widgets"][0]["field_name"] == FIELD_PARENT_KIDS
    assert both_info["page_annots"][0]["widgets"][0]["field_name"] == FIELD_PARENT_KIDS
    assert both_info["page_annots"][1]["widgets"][0]["field_name"] == FIELD_PARENT_KIDS

    _assert_form_structure_valid(page1, {FIELD_PARENT_KIDS})
    _assert_form_structure_valid(page2, {FIELD_PARENT_KIDS})
    _assert_form_structure_valid(both, {FIELD_PARENT_KIDS})

    with pikepdf.open(page1, suppress_warnings=True) as pdf:
        parent = pdf.Root["/AcroForm"]["/Fields"][0]
        assert len(parent["/Kids"]) == 1
        assert parent["/Kids"][0].get("/P") == pdf.pages[0].obj
    with pikepdf.open(both, suppress_warnings=True) as pdf:
        parent = pdf.Root["/AcroForm"]["/Fields"][0]
        assert len(parent["/Kids"]) == 2
        assert {_object_key(kid.get("/P")) for kid in parent["/Kids"]} == {
            _object_key(page.obj) for page in pdf.pages
        }


def _patch_compress_analyze_thumbnail(monkeypatch):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "_generate_page_thumbnail",
        lambda _pdf_path, page_index: f"data:image/svg+xml;base64,page-{page_index + 1}",
    )
    return compress_routes


def _compress_analyze(client, fixture_pdf: Path, monkeypatch) -> str:
    _patch_compress_analyze_thumbnail(monkeypatch)
    response = client.post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(fixture_pdf.read_bytes()), "gv-fixture.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["total_pages"] == 2
    return payload["analyse_id"]


def _compress_modern_output_with_mocked_engine(app, fixture_pdf: Path, tmp_path: Path, monkeypatch, name: str):
    compress_routes = _patch_compress_analyze_thumbnail(monkeypatch)
    received = {}

    def fake_compress_group(input_path, output_path, pages, quality, dpi, resize_to_a4=False, rotations=None):
        received["input_info"] = inspect_pdf(input_path)
        received["input_stats"] = signature_region_stats(input_path)
        shutil.copyfile(input_path, output_path)
        return []

    monkeypatch.setattr(compress_routes, "comprimir_pdf_com_params", fake_compress_group)
    client = app.test_client()
    analyse_id = _compress_analyze(client, fixture_pdf, monkeypatch)

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
    return _write_response_pdf(response, tmp_path / name), received


def test_compress_modern_keep_original_uses_sanitized_source(app, fixture_pdf, tmp_path, monkeypatch):
    client = app.test_client()
    analyse_id = _compress_analyze(client, fixture_pdf, monkeypatch)

    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": [
                {"page_number": 1, "include": True, "keep_original": True},
                {"page_number": 2, "include": True, "keep_original": True},
            ],
        },
        headers={"Accept": "application/pdf"},
    )
    output = _write_response_pdf(response, tmp_path / "compress_keep_original.pdf")
    info = inspect_pdf(output)

    assert info["page_count"] == 2
    assert PAGE1_TEXT in extract_text(output)
    assert PAGE2_TEXT in extract_text(output)
    _assert_dangerous_content_removed(info)


def test_compress_modern_interactive_skips_mocked_engine_and_preserves_output(
    app, fixture_pdf, tmp_path, monkeypatch
):
    output, received = _compress_modern_output_with_mocked_engine(
        app, fixture_pdf, tmp_path, monkeypatch, "compress_mocked_engine.pdf"
    )
    output_info = inspect_pdf(output)

    assert received == {}
    _assert_dangerous_content_removed(output_info)
    _assert_two_page_legitimate_content(output_info)
    assert signature_region_stats(output)["non_white_ratio"] > 0.01


def test_compress_modern_preserves_filled_text_fields_requirement(app, fixture_pdf, tmp_path, monkeypatch):
    output, _received = _compress_modern_output_with_mocked_engine(
        app, fixture_pdf, tmp_path, monkeypatch, "compress_preserve_fields.pdf"
    )
    info = inspect_pdf(output)

    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2


def test_compress_modern_preserves_signature_widget_appearance_requirement(
    app, fixture_pdf, tmp_path, monkeypatch
):
    output, _received = _compress_modern_output_with_mocked_engine(
        app, fixture_pdf, tmp_path, monkeypatch, "compress_preserve_signature.pdf"
    )
    info = inspect_pdf(output)

    _assert_two_page_legitimate_content(info)
    assert signature_region_stats(output)["non_white_ratio"] > 0.01


def _compress_legacy_output_with_mocked_heavy(app, fixture_pdf: Path, monkeypatch):
    captured = {}

    def fake_qpdf_flatten(src, dst):
        captured["heavy_input_info"] = inspect_pdf(src)
        captured["heavy_input_stats"] = signature_region_stats(src)
        shutil.copyfile(src, dst)

    monkeypatch.setattr(
        compress_service,
        "_qpdf_flatten",
        fake_qpdf_flatten,
    )
    monkeypatch.setattr(
        compress_service,
        "_run_ghostscript",
        lambda input_pdf, output_pdf, quality, dpi: shutil.copyfile(input_pdf, output_pdf),
    )

    with app.app_context():
        output, warnings = compress_service.comprimir_pdf(_as_filestorage(fixture_pdf))
    return Path(output), warnings, captured


def test_compress_legacy_mocked_heavy_steps_uses_sanitized_source(
    app, fixture_pdf, monkeypatch
):
    output, warnings, captured = _compress_legacy_output_with_mocked_heavy(
        app, fixture_pdf, monkeypatch
    )
    info = inspect_pdf(output)

    assert PDF_PRESERVATION_WARNING in warnings
    assert PDF_SIGNATURE_REWRITE_WARNING in warnings
    assert captured == {}
    _assert_dangerous_content_removed(info)
    _assert_two_page_legitimate_content(info)
    assert signature_region_stats(output)["non_white_ratio"] > 0.01


def test_compress_legacy_preserves_filled_text_fields_requirement(app, fixture_pdf, monkeypatch):
    output, _warnings, _captured = _compress_legacy_output_with_mocked_heavy(
        app, fixture_pdf, monkeypatch
    )

    info = inspect_pdf(output)

    assert info["fields"][FIELD_PAGE_1]["value"] == VALUE_PAGE_1
    assert info["fields"][FIELD_PAGE_2]["value"] == VALUE_PAGE_2
