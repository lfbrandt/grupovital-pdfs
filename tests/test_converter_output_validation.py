import csv
import io
import os
import zipfile
from pathlib import Path

import pytest
from PyPDF2 import PdfWriter

from app.services.converter_output_validation import (
    ConverterOutputValidationError,
    validate_converter_output,
)


def _write_pdf(path: Path, *, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as stream:
        writer.write(stream)


def _write_docx(path: Path, *, include_document: bool = True) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/'
                'package/2006/content-types"></Types>'
            ),
        )
        if include_document:
            archive.writestr(
                "word/document.xml",
                (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body/></w:document>'
                ),
            )


def _write_workbook(path: Path, *, macro_enabled: bool = False) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active["A1"] = "valor"
    workbook.save(path)
    workbook.close()
    if not macro_enabled:
        return

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


@pytest.mark.parametrize(
    ("target", "writer"),
    [
        ("pdf", lambda path: _write_pdf(path)),
        ("docx", lambda path: _write_docx(path)),
        ("xlsx", lambda path: _write_workbook(path)),
        ("xlsm", lambda path: _write_workbook(path, macro_enabled=True)),
        (
            "csv",
            lambda path: path.write_text(
                "codigo,valor\n001,10\n",
                encoding="utf-8",
            ),
        ),
    ],
)
def test_valid_structural_outputs_are_accepted(tmp_path, target, writer):
    output = tmp_path / f"resultado.{target}"
    writer(output)

    assert (
        validate_converter_output(str(output), str(tmp_path), target)
        == str(output.resolve())
    )


def test_pdf_truncated_is_rejected(tmp_path):
    output = tmp_path / "resultado.pdf"
    output.write_bytes(b"%PDF-1.7\nobjeto truncado")

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "pdf")


def test_pdf_without_pages_is_rejected(tmp_path):
    import pikepdf

    output = tmp_path / "resultado.pdf"
    with pikepdf.new() as document:
        document.save(output)

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "pdf")


@pytest.mark.parametrize(
    "writer",
    [
        lambda path: path.write_bytes(b"not-a-zip"),
        lambda path: _write_docx(path, include_document=False),
    ],
)
def test_invalid_docx_is_rejected(tmp_path, writer):
    output = tmp_path / "resultado.docx"
    writer(output)

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "docx")


def test_docx_with_bad_zip_crc_is_rejected(tmp_path):
    output = tmp_path / "resultado.docx"
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            "<document>conteudo-original</document>",
        )
    payload = output.read_bytes()
    marker = b"conteudo-original"
    marker_index = payload.index(marker)
    corrupted = (
        payload[:marker_index]
        + b"conteudo-alterado!"
        + payload[marker_index + len(marker):]
    )
    output.write_bytes(corrupted)

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "docx")


def test_truncated_xlsx_is_rejected(tmp_path):
    output = tmp_path / "resultado.xlsx"
    _write_workbook(output)
    output.write_bytes(output.read_bytes()[:80])

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "xlsx")


def test_xlsx_without_workbook_xml_is_rejected(tmp_path):
    output = tmp_path / "resultado.xlsx"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "xlsx")


def test_structurally_valid_but_empty_xlsx_is_rejected(tmp_path):
    from openpyxl import Workbook

    output = tmp_path / "resultado.xlsx"
    workbook = Workbook()
    workbook.save(output)
    workbook.close()

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "xlsx")


def test_xlsx_renamed_to_xlsm_is_rejected(tmp_path):
    output = tmp_path / "resultado.xlsm"
    _write_workbook(output)

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "xlsm")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"codigo\x00valor\n",
        b"\xff\xfe\x00\x00",
    ],
)
def test_invalid_csv_is_rejected(tmp_path, payload):
    output = tmp_path / "resultado.csv"
    output.write_bytes(payload)

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "csv")


def test_csv_with_few_rows_and_no_header_requirement_is_accepted(tmp_path):
    output = tmp_path / "resultado.csv"
    with output.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerow(["001"])

    assert validate_converter_output(
        str(output),
        str(tmp_path),
        "csv",
    ) == str(output.resolve())


def test_outside_missing_empty_and_wrong_extension_are_rejected(tmp_path):
    workdir = tmp_path / "job"
    workdir.mkdir()
    outside = tmp_path / "outside.pdf"
    _write_pdf(outside)
    empty = workdir / "empty.pdf"
    empty.touch()
    wrong_extension = workdir / "resultado.bin"
    _write_pdf(wrong_extension)

    for path in (
        outside,
        workdir / "missing.pdf",
        empty,
        wrong_extension,
    ):
        with pytest.raises(ConverterOutputValidationError):
            validate_converter_output(str(path), str(workdir), "pdf")


def test_symlink_output_is_rejected_when_supported(tmp_path):
    source = tmp_path / "source.pdf"
    _write_pdf(source)
    workdir = tmp_path / "job"
    workdir.mkdir()
    link = workdir / "resultado.pdf"
    try:
        os.symlink(source, link)
    except (NotImplementedError, OSError):
        pytest.skip("Symlink indisponível nesta plataforma/conta.")

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(link), str(workdir), "pdf")


def test_deadline_callback_runs_before_and_after_structure_check(tmp_path):
    output = tmp_path / "resultado.pdf"
    _write_pdf(output)
    calls = []

    validate_converter_output(
        str(output),
        str(tmp_path),
        "pdf",
        check_deadline=lambda: calls.append("check"),
    )

    assert calls == ["check", "check"]


@pytest.mark.parametrize(
    "payload",
    [
        b"\xef\xbb\xbf",
        b"   \t\r\n",
        b"\r\n\n",
        b",,,\n",
        b";;;\n",
        b"<!DOCTYPE html><html><body>failure</body></html>\n",
        b'{"error":"synthetic failure"}\n',
        b"Traceback (most recent call last):\nRuntimeError: synthetic\n",
        "Falha ao extrair conteúdo do PDF.\n".encode("utf-8"),
        b"GVCSV_PARTIAL_INVALID\ncodigo,valor\n1,2\n",
        b"codigo,\x01valor\n",
    ],
)
def test_csv_semantically_empty_diagnostic_or_binary_output_is_rejected(
    tmp_path,
    payload,
):
    output = tmp_path / "resultado.csv"
    output.write_bytes(payload)

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "csv")


def test_csv_with_inconsistent_columns_is_rejected(tmp_path):
    output = tmp_path / "resultado.csv"
    output.write_text(
        "codigo,valor\n001,10\n002\n",
        encoding="utf-8",
    )

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(str(output), str(tmp_path), "csv")


@pytest.mark.parametrize("delimiter", [",", ";"])
def test_valid_csv_with_quotes_delimiter_and_internal_newline_is_accepted(
    tmp_path,
    delimiter,
):
    output = tmp_path / "resultado.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(
            stream,
            delimiter=delimiter,
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writerow(["codigo", "descricao"])
        writer.writerow(["001", f"parte{delimiter}interna"])
        writer.writerow(["002", "linha 1\nlinha 2"])

    assert validate_converter_output(
        str(output),
        str(tmp_path),
        "csv",
    ) == str(output.resolve())


def test_pdf_csv_context_rejects_header_without_data(tmp_path):
    output = tmp_path / "resultado.csv"
    output.write_text("cabecalho\n", encoding="utf-8")

    with pytest.raises(ConverterOutputValidationError):
        validate_converter_output(
            str(output),
            str(tmp_path),
            "csv",
            source_ext="pdf",
            require_table_data=True,
        )


def test_pdf_csv_context_accepts_legitimate_one_column_table(tmp_path):
    output = tmp_path / "resultado.csv"
    output.write_text("cabecalho\nvalor\n", encoding="utf-8")

    assert validate_converter_output(
        str(output),
        str(tmp_path),
        "csv",
        source_ext="pdf",
        require_table_data=True,
    ) == str(output.resolve())
