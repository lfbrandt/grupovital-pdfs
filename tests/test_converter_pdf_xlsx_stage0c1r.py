from pathlib import Path

import pandas as pd
import pytest
from PyPDF2 import PdfWriter
from werkzeug.exceptions import BadRequest

from app.services import converter_service


def _rows(count, *, prefix="R"):
    return [
        [f"{prefix}-{index:03d}", f"Descrição {index}", f"R$ {index + 1},00"]
        for index in range(count)
    ]


def _candidate(
    rows,
    *,
    page=1,
    extractor="camelot-lattice-smart",
    bbox=(10, 10, 200, 200),
    region_id="region",
):
    return converter_service._make_table_candidate(
        pd.DataFrame(rows),
        page_number=page,
        extractor=extractor,
        bbox=bbox,
        region_id=region_id,
        scope="region" if bbox is not None else "page",
    )


def test_clean_post_normalization_is_accepted():
    before = pd.DataFrame(_rows(12))
    after = before.copy()

    assessment = converter_service._assess_post_normalization(
        before,
        after,
    )

    assert assessment.accepted is True
    assert assessment.reasons == ()


def test_clean_specialized_normalization_is_used(monkeypatch):
    rows = [["Código", "Descrição", "Valor"]] + _rows(12)
    candidate = _candidate(rows)
    normalized = pd.DataFrame(_rows(12, prefix="N"))

    monkeypatch.setattr(
        converter_service,
        "_looks_like_coparticipacao_table",
        lambda _dataframe: True,
    )
    monkeypatch.setattr(
        converter_service,
        "_normalize_coparticipacao_table",
        lambda _dataframe: normalized.copy(),
    )

    prepared = converter_service._prepare_candidate_for_workbook(
        candidate,
        sheet_name="Table 1",
        allow_specialized_normalization=True,
    )

    assert prepared.normalization_accepted is True
    pd.testing.assert_frame_equal(prepared.dataframe, normalized)


def test_post_normalization_91_to_586_is_rejected():
    unique = _rows(91)
    before = pd.DataFrame(unique)
    after = pd.DataFrame(unique * 6 + unique[:40])

    assessment = converter_service._assess_post_normalization(
        before,
        after,
    )

    assert assessment.before["useful_rows"] == 91
    assert assessment.after["useful_rows"] == 586
    assert assessment.after["unique_rows"] == 91
    assert assessment.accepted is False
    assert "row_growth_without_information" in assessment.reasons
    assert "mass_duplication" in assessment.reasons


def test_legitimate_growth_with_new_information_is_not_rejected_by_size():
    before = pd.DataFrame(_rows(10))
    after = pd.DataFrame([
        [f"R-{source:03d}-{part}", f"Parte {part}", source * 10 + part]
        for source in range(10)
        for part in range(3)
    ])

    assessment = converter_service._assess_post_normalization(
        before,
        after,
    )

    assert assessment.after["useful_rows"] == 30
    assert assessment.after["unique_rows"] == 30
    assert assessment.accepted is True


def test_duplicate_growth_without_distinct_growth_is_detected():
    unique = _rows(20)
    assessment = converter_service._assess_post_normalization(
        pd.DataFrame(unique),
        pd.DataFrame(unique * 4),
    )

    assert assessment.accepted is False
    assert "duplicate_growth" in assessment.reasons


def test_rejected_normalization_falls_back_to_valid_pre_normalization(
    monkeypatch,
):
    rows = [["Código", "Descrição", "Valor"]] + _rows(12)
    candidate = _candidate(rows)
    inflated = pd.DataFrame(rows * 6 + rows[:8])

    monkeypatch.setattr(
        converter_service,
        "_looks_like_coparticipacao_table",
        lambda _dataframe: True,
    )
    monkeypatch.setattr(
        converter_service,
        "_normalize_coparticipacao_table",
        lambda _dataframe: inflated.copy(),
    )

    prepared = converter_service._prepare_candidate_for_workbook(
        candidate,
        sheet_name="Table 1",
        allow_specialized_normalization=True,
    )

    assert prepared.normalization_accepted is False
    assert len(prepared.dataframe) == 12
    assert prepared.sheet_name == "Table 1"


def test_invalid_pre_and_post_normalization_stop_before_workbook(
    monkeypatch,
):
    candidate = _candidate([["Código", "Descrição"], ["001", "Valor"]])
    empty = pd.DataFrame()

    monkeypatch.setattr(
        converter_service,
        "_looks_like_coparticipacao_table",
        lambda _dataframe: True,
    )
    monkeypatch.setattr(
        converter_service,
        "_clean_and_infer",
        lambda _dataframe: (empty.copy(), {}),
    )
    monkeypatch.setattr(
        converter_service,
        "_normalize_coparticipacao_table",
        lambda _dataframe: empty.copy(),
    )

    with pytest.raises(BadRequest):
        converter_service._prepare_candidate_for_workbook(
            candidate,
            sheet_name="Table 1",
            allow_specialized_normalization=True,
        )


def test_invalid_final_tables_do_not_open_excel_writer(
    tmp_path,
    monkeypatch,
):
    input_pdf = tmp_path / "controlled.pdf"
    input_pdf.write_bytes(b"%PDF-controlled")
    candidate = _candidate([
        ["Código", "Descrição"],
        ["001", "Valor"],
    ])
    writer_calls = []

    monkeypatch.setenv("PDF_TO_XLSX_MODEL_STYLE", "1")
    monkeypatch.setenv("PDF_TO_XLSX_USE_SMART_BBOX", "1")
    monkeypatch.setenv("PDF_TO_XLSX_ALWAYS_AREAS", "0")
    monkeypatch.setattr(
        converter_service,
        "enforce_pdf_page_limit",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        converter_service,
        "_prepare_camelot_env",
        lambda: None,
    )
    monkeypatch.setattr(
        converter_service,
        "_pdf_has_selectable_text",
        lambda _path: True,
    )
    monkeypatch.setattr(
        converter_service,
        "_extract_tables_smart",
        lambda _path: [candidate],
    )
    monkeypatch.setattr(
        converter_service,
        "_run_pdf_xlsx_fallback_pass",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        converter_service,
        "_looks_like_coparticipacao_table",
        lambda _dataframe: True,
    )
    monkeypatch.setattr(
        converter_service,
        "_clean_and_infer",
        lambda _dataframe: (pd.DataFrame(), {}),
    )
    monkeypatch.setattr(
        converter_service,
        "_normalize_coparticipacao_table",
        lambda _dataframe: pd.DataFrame(),
    )
    monkeypatch.setattr(
        pd,
        "ExcelWriter",
        lambda *_args, **_kwargs: writer_calls.append(True),
    )

    with pytest.raises(BadRequest):
        converter_service._pdf_to_xlsx(
            str(input_pdf),
            str(tmp_path / "out"),
        )

    assert writer_calls == []
    assert list((tmp_path / "out").glob("*.xlsx")) == []


def test_quality_metrics_do_not_mutate_normalization_values():
    before = pd.DataFrame([
        ["001", "R$ 1.234,50", "12,5%"],
        ["002", "R$ 200,00", "7,5%"],
    ])
    after = before.copy(deep=True)
    expected_before = before.copy(deep=True)
    expected_after = after.copy(deep=True)

    converter_service._assess_post_normalization(before, after)

    pd.testing.assert_frame_equal(before, expected_before)
    pd.testing.assert_frame_equal(after, expected_after)


def test_missing_second_page_runs_stream_only_for_that_page(monkeypatch):
    first = _candidate(_rows(50), page=1, region_id="first")
    recovered = _candidate(
        _rows(40, prefix="S"),
        page=2,
        extractor="camelot-stream-global",
        bbox=None,
        region_id="second",
    )
    calls = {"stream": [], "plumber": []}

    def fake_stream(_path, pages):
        calls["stream"].append(pages)
        return [recovered]

    def fake_plumber(_path, pages):
        calls["plumber"].append(pages)
        return []

    monkeypatch.setattr(converter_service, "_rescue_with_stream", fake_stream)
    monkeypatch.setattr(
        converter_service,
        "_pdfplumber_tables_dfs",
        fake_plumber,
    )

    result = converter_service._run_pdf_xlsx_fallback_pass(
        "controlled.pdf",
        [1, 2],
        [first],
        allow_stream=True,
    )

    assert calls == {"stream": ["2"], "plumber": []}
    assert [candidate.page_number for candidate in result] == [2]
    selected = converter_service._select_candidate_winners([first] + result)
    assert {candidate.page_number for candidate in selected} == {1, 2}


def test_missing_empty_page_does_not_create_artificial_candidate(monkeypatch):
    first = _candidate(_rows(50), page=1)
    calls = []
    monkeypatch.setattr(
        converter_service,
        "_pdfplumber_tables_dfs",
        lambda _path, pages: calls.append(pages) or [],
    )

    recovered = converter_service._run_pdf_xlsx_fallback_pass(
        "controlled.pdf",
        [1, 2],
        [first],
        allow_stream=False,
    )

    assert calls == ["2"]
    assert recovered == []
    assert first.page_number == 1


def test_fallback_pass_does_not_loop_or_reprocess_represented_page(
    monkeypatch,
):
    first = _candidate(_rows(50), page=1)
    calls = {"stream": 0, "plumber": 0}

    def fake_stream(_path, pages):
        calls["stream"] += 1
        assert pages == "2"
        return []

    def fake_plumber(_path, pages):
        calls["plumber"] += 1
        assert pages == "2"
        return []

    monkeypatch.setattr(converter_service, "_rescue_with_stream", fake_stream)
    monkeypatch.setattr(
        converter_service,
        "_pdfplumber_tables_dfs",
        fake_plumber,
    )

    converter_service._run_pdf_xlsx_fallback_pass(
        "controlled.pdf",
        [1, 2],
        [first],
        allow_stream=True,
    )

    assert calls == {"stream": 1, "plumber": 1}


def test_represented_low_score_page_is_not_reprocessed(monkeypatch):
    represented = _candidate(
        [["fragmento", ""]],
        page=1,
        bbox=None,
    )
    represented.score = 0.0
    calls = []
    monkeypatch.setattr(
        converter_service,
        "_pdfplumber_tables_dfs",
        lambda _path, pages: calls.append(pages) or [],
    )

    recovered = converter_service._run_pdf_xlsx_fallback_pass(
        "controlled.pdf",
        [1],
        [represented],
        allow_stream=False,
    )

    assert recovered == []
    assert calls == []


def test_fallback_checks_total_deadline(monkeypatch):
    first = _candidate(_rows(50), page=1)

    def expired(_stage):
        raise converter_service.ConverterTimeoutError("expired")

    monkeypatch.setattr(
        converter_service,
        "check_converter_deadline",
        expired,
    )

    with pytest.raises(converter_service.ConverterTimeoutError):
        converter_service._run_pdf_xlsx_fallback_pass(
            "controlled.pdf",
            [1, 2],
            [first],
            allow_stream=False,
        )


def test_requested_page_resolution_is_explicit_and_bounded():
    assert converter_service._resolve_requested_pdf_pages(5, None) == [
        1, 2, 3, 4, 5
    ]
    assert converter_service._resolve_requested_pdf_pages(
        5,
        "1,3-4,9,invalid",
    ) == [1, 3, 4]


def test_blank_page_has_no_structured_text_fallback(tmp_path):
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    assert converter_service._structured_text_fallback_candidates(
        str(pdf_path),
        [1],
    ) == []


def test_contiguous_repeated_block_is_collapsed_preserving_first_and_order():
    block = _rows(5)
    tail = [["TAIL", "Final", "R$ 99,00"]]
    candidate = _candidate(block * 3 + tail)

    selected = converter_service._select_and_deduplicate_candidates(
        [candidate],
        emit_logs=False,
    )[0]

    assert selected.repeated_block_rows_removed == 10
    assert selected.dataframe.values.tolist() == block + tail


def test_repeated_recognized_header_is_removed_without_removing_data():
    header = ["Código", "Descrição", "Valor"]
    first = _rows(3)
    second = _rows(3, prefix="S")
    candidate = _candidate([header] + first + [header] + second)

    selected = converter_service._select_and_deduplicate_candidates(
        [candidate],
        emit_logs=False,
    )[0]

    assert selected.repeated_header_rows_removed == 1
    assert selected.dataframe.values.tolist() == [header] + first + second


def test_ambiguous_repeated_text_row_is_preserved_as_legitimate_data():
    repeated = ["Plano Alpha", "Categoria Especial", "Ativo"]
    rows = [
        repeated,
        ["Plano Beta", "Categoria Normal", "Ativo"],
        repeated,
        ["Plano Gama", "Categoria Normal", "Ativo"],
    ]
    candidate = _candidate(rows)

    selected = converter_service._select_and_deduplicate_candidates(
        [candidate],
        emit_logs=False,
    )[0]

    assert selected.repeated_header_rows_removed == 0
    assert selected.dataframe.values.tolist() == rows


def test_mass_frequency_cleanup_requires_and_uses_additional_evidence():
    unique = _rows(10)
    inflated = []
    for offset in range(6):
        inflated.extend(unique[offset:] + unique[:offset])
    candidate = _candidate(inflated)

    selected = converter_service._select_and_deduplicate_candidates(
        [candidate],
        emit_logs=False,
    )[0]

    assert selected.frequency_rows_removed > 0
    assert selected.useful_rows == 10
    assert selected.dataframe.values.tolist() == unique


def test_final_table_rejects_massive_residual_duplication():
    unique = _rows(10)
    with pytest.raises(BadRequest):
        converter_service._validate_final_pdf_xlsx_table(
            pd.DataFrame(unique * 4),
            page_number=1,
            group_id="p0001-g001",
            extractor="controlled",
        )


def test_consolidation_allows_equal_rows_from_independent_sources():
    first = pd.DataFrame(_rows(10))
    second = first.copy()
    third = first.copy()
    consolidated = pd.concat(
        [first, second, third],
        ignore_index=True,
    )

    converter_service._validate_pdf_xlsx_consolidation(
        [first, second, third],
        consolidated,
    )


def test_consolidation_rejects_rows_without_source_correspondence():
    source = pd.DataFrame(_rows(10))
    inflated = pd.concat([source] * 3, ignore_index=True)

    with pytest.raises(BadRequest):
        converter_service._validate_pdf_xlsx_consolidation(
            [source],
            inflated,
        )
