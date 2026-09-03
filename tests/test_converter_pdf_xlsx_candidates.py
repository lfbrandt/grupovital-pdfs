from pathlib import Path

import pandas as pd
import pytest
from werkzeug.exceptions import BadRequest

from app.services import converter_service


def _candidate(
    rows,
    *,
    page=1,
    extractor="camelot-lattice-smart",
    bbox=(10, 10, 200, 200),
    region_id="region",
    scope="region",
):
    return converter_service._make_table_candidate(
        pd.DataFrame(rows),
        page_number=page,
        extractor=extractor,
        bbox=bbox,
        region_id=region_id,
        scope=scope,
    )


def _clean_rows(count=12):
    return [
        [f"{index:03d}", f"Procedimento {index}", f"R$ {index + 1},00"]
        for index in range(count)
    ]


def test_inflated_586_row_candidate_loses_to_91_unique_rows():
    unique_rows = _clean_rows(91)
    inflated_rows = unique_rows * 6 + unique_rows[:40]
    assert len(inflated_rows) == 586

    inflated = _candidate(
        inflated_rows,
        extractor="camelot-lattice-smart",
        region_id="inflated",
    )
    clean = _candidate(
        unique_rows,
        extractor="pdfplumber",
        region_id="clean",
    )

    assert inflated.unique_rows == 91
    assert inflated.duplicate_rows == 495
    assert inflated.duplicate_ratio == pytest.approx(495 / 586)
    assert clean.score > inflated.score

    selected = converter_service._select_and_deduplicate_candidates(
        [inflated, clean],
        emit_logs=False,
    )

    assert len(selected) == 1
    assert selected[0].extractor == "pdfplumber"
    assert selected[0].useful_rows == 91


def test_isolated_exact_duplicates_inside_candidate_are_preserved():
    rows = [
        ["Código", "Valor"],
        ["001", "R$ 10,00"],
        ["002", "R$ 20,00"],
        ["001", "R$ 10,00"],
        ["003", "R$ 30,00"],
        ["002", "R$ 20,00"],
    ]
    selected = converter_service._select_and_deduplicate_candidates(
        [_candidate(rows)],
        emit_logs=False,
    )

    assert selected[0].duplicate_rows_removed == 0
    assert selected[0].dataframe.values.tolist() == rows


def test_massively_duplicated_single_candidate_is_cleaned_not_exported_inflated():
    unique_rows = _clean_rows(91)
    inflated_rows = unique_rows * 6 + unique_rows[:40]

    selected = converter_service._select_and_deduplicate_candidates(
        [_candidate(inflated_rows)],
        emit_logs=False,
    )

    assert len(selected) == 1
    assert selected[0].duplicate_rows_removed == 495
    assert selected[0].useful_rows == 91
    assert selected[0].duplicate_ratio == 0
    converter_service._validate_selected_pdf_xlsx_candidates(selected)


def test_equal_rows_on_different_pages_are_preserved():
    rows = _clean_rows(8)
    selected = converter_service._select_and_deduplicate_candidates(
        [
            _candidate(rows, page=1, region_id="page-1"),
            _candidate(rows, page=2, region_id="page-2"),
        ],
        emit_logs=False,
    )

    assert [candidate.page_number for candidate in selected] == [1, 2]
    assert all(candidate.useful_rows == 8 for candidate in selected)


def test_equal_rows_in_non_overlapping_tables_are_preserved():
    rows = _clean_rows(8)
    selected = converter_service._select_and_deduplicate_candidates(
        [
            _candidate(rows, bbox=(10, 300, 200, 500), region_id="top"),
            _candidate(rows, bbox=(10, 20, 200, 200), region_id="bottom"),
        ],
        emit_logs=False,
    )

    assert len(selected) == 2
    assert {candidate.region_id for candidate in selected} == {"top", "bottom"}


def test_overlapping_lattice_stream_and_pdfplumber_have_one_winner():
    rows = _clean_rows(15)
    candidates = [
        _candidate(
            rows,
            extractor="camelot-lattice-smart",
            bbox=(10, 10, 200, 200),
            region_id="lattice",
        ),
        _candidate(
            rows,
            extractor="camelot-stream-smart",
            bbox=(12, 12, 198, 198),
            region_id="stream",
        ),
        _candidate(
            rows,
            extractor="pdfplumber",
            bbox=(9, 9, 201, 201),
            region_id="pdfplumber",
        ),
    ]

    selected = converter_service._select_and_deduplicate_candidates(
        candidates,
        emit_logs=False,
    )

    assert len(selected) == 1
    assert selected[0].extractor == "camelot-lattice-smart"


def test_full_page_candidate_does_not_connect_independent_regions():
    first = _candidate(
        _clean_rows(10),
        bbox=(10, 300, 200, 500),
        region_id="first",
    )
    second = _candidate(
        _clean_rows(10),
        bbox=(10, 20, 200, 200),
        region_id="second",
    )
    full_page = _candidate(
        _clean_rows(20),
        extractor="camelot-stream-global",
        bbox=None,
        region_id="full",
        scope="page",
    )

    selected = converter_service._select_and_deduplicate_candidates(
        [first, full_page, second],
        emit_logs=False,
    )

    assert len(selected) == 2
    assert {candidate.region_id for candidate in selected} == {
        "first",
        "second",
    }


def test_repeated_headers_are_penalized():
    data = _clean_rows(20)
    header = ["Código", "Procedimento", "Valor"]
    clean = _candidate([header] + data, region_id="clean")
    repeated = _candidate(
        [header] + data[:10] + [header] * 8 + data[10:],
        extractor="camelot-stream-smart",
        region_id="repeated",
    )

    assert repeated.repeated_header_rows >= 8
    assert repeated.score < clean.score


def test_footers_and_near_empty_rows_are_penalized():
    data = _clean_rows(20)
    clean = _candidate(data, region_id="clean")
    degraded = _candidate(
        data
        + [["Obs.: valores sujeitos a alteração", "", ""] for _ in range(5)]
        + [["fragmento", "", ""] for _ in range(8)],
        extractor="camelot-stream-smart",
        region_id="degraded",
    )

    assert degraded.boilerplate_ratio > clean.boilerplate_ratio
    assert degraded.near_empty_ratio > clean.near_empty_ratio
    assert degraded.score < clean.score


def test_comparison_normalization_handles_brl_and_percentage_safely():
    assert converter_service._comparison_cell(None) == ""
    assert converter_service._comparison_cell(float("nan")) == ""
    assert converter_service._comparison_cell(pd.NA) == ""
    assert converter_service._comparison_cell(1.0) == "number:1"
    assert converter_service._comparison_cell(" R$ 1.234,50 ") == (
        "currency:1234.5"
    )
    assert converter_service._comparison_cell("12,5%") == "percent:0.125"

    candidate = _candidate([
        ["Código", "Valor", "Percentual"],
        ["001", "R$ 1.234,50", "12,5%"],
        ["001", "R$ 1234,50", "12,5%"],
    ])
    selected = converter_service._select_and_deduplicate_candidates(
        [candidate],
        emit_logs=False,
    )

    assert selected[0].duplicate_rows_removed == 0
    assert selected[0].dataframe.iloc[1].tolist() == [
        "001",
        "R$ 1.234,50",
        "12,5%",
    ]
    assert selected[0].dataframe.iloc[2].tolist() == [
        "001",
        "R$ 1234,50",
        "12,5%",
    ]


def test_semantically_similar_but_not_exact_rows_are_not_removed():
    candidate = _candidate([
        ["Código", "Descrição"],
        ["001", "Procedimento A"],
        ["001", "procedimento a"],
        ["001", "Procedimento-A"],
    ])

    selected = converter_service._select_and_deduplicate_candidates(
        [candidate],
        emit_logs=False,
    )

    assert selected[0].duplicate_rows_removed == 0
    assert selected[0].useful_rows == 4


def test_empty_candidate_selection_is_rejected():
    with pytest.raises(BadRequest):
        converter_service._validate_selected_pdf_xlsx_candidates([])


def test_pdf_without_selectable_text_keeps_ocr_fallback(
    tmp_path,
    monkeypatch,
):
    input_pdf = tmp_path / "scan.pdf"
    input_pdf.write_bytes(b"%PDF-controlled-test")
    ocr_calls = []

    monkeypatch.setenv("PDF_TO_XLSX_MODEL_STYLE", "1")
    monkeypatch.setenv("PDF_TO_XLSX_USE_SMART_BBOX", "1")
    monkeypatch.setenv("PDF_TO_XLSX_ALWAYS_AREAS", "0")
    monkeypatch.setenv("PDF_TO_XLSX_ALLOW_STREAM", "0")
    monkeypatch.setattr(
        converter_service,
        "enforce_pdf_page_limit",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(converter_service, "_prepare_camelot_env", lambda: None)
    monkeypatch.setattr(
        converter_service,
        "_pdf_has_selectable_text",
        lambda _path: False,
    )

    def fake_ocr(path):
        ocr_calls.append(path)
        return path

    monkeypatch.setattr(converter_service, "_try_ocr", fake_ocr)
    monkeypatch.setattr(
        converter_service,
        "_extract_tables_smart",
        lambda _path: [_candidate([
            ["Código", "Valor"],
            ["001", "R$ 10,00"],
            ["002", "R$ 20,00"],
        ])],
    )

    output = converter_service._pdf_to_xlsx(
        str(input_pdf),
        str(tmp_path),
    )

    assert ocr_calls == [str(input_pdf)]
    assert Path(output).is_file()
