import csv
import inspect
import io
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from PyPDF2 import PdfWriter

from app import create_app
from app.services import converter_service


NO_TABLE_RESPONSE = {
    "error": "Nenhuma tabela utilizável foi encontrada no PDF."
}
EXTRACTION_RESPONSE = {
    "error": "Não foi possível extrair uma tabela válida do PDF."
}
INVALID_OUTPUT_RESPONSE = {
    "error": "A conversão não gerou um arquivo válido para download."
}


class _Tables:
    def __init__(self, frames):
        self.tables = [SimpleNamespace(df=frame) for frame in frames]
        self.n = len(self.tables)


class _Page:
    def __init__(self, tables=None, text=""):
        self._tables = [] if tables is None else tables
        self._text = text

    def extract_tables(self):
        return self._tables

    def extract_text(self):
        return self._text


class _PdfContext:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


@pytest.fixture
def app(tmp_path, monkeypatch):
    import app as app_package

    monkeypatch.setattr(
        app_package,
        "_bootstrap_dotenv",
        lambda: ("testing", "(pdf-csv-hardening-test)"),
    )
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FILE_LOG_LEVEL", "CRITICAL")
    monkeypatch.setenv("CONSOLE_LOG_LEVEL", "CRITICAL")
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=str(tmp_path),
        CONVERTER_MAX_FILES=2,
        CONVERTER_MAX_RUNTIME_SEC=30,
    )
    return application


def _pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _upload():
    return {
        "files[]": (
            io.BytesIO(_pdf_bytes()),
            "synthetic.pdf",
        )
    }


def _generated_files(upload_folder):
    generated = Path(upload_folder) / "generated"
    if not generated.exists():
        return []
    return sorted(path for path in generated.rglob("*") if path.is_file())


def _patch_prerequisites(monkeypatch):
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


def test_pdf_table_generates_csv_http_200_and_preserves_formula_policy(
    app,
    monkeypatch,
    caplog,
):
    import camelot
    import pdfplumber

    payloads = [
        "=1+1",
        "+SUM(A1:A2)",
        "-10+20",
        "@SUM(A1:A2)",
        "   =1+1",
        "\t=1+1",
        "\r=1+1",
        "\n=1+1",
        '=HYPERLINK("https://example.invalid";"open")',
        "='file:///synthetic.invalid'#$Sheet1.A1",
        '=DDE("synthetic.invalid";"topic";"item")',
        "-10",
        "-10.5",
        "-1E2",
        "-1E2+3",
        "texto seguro",
    ]
    frame = pd.DataFrame([["cabecalho"], *[[value] for value in payloads]])
    _patch_prerequisites(monkeypatch)
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: _Tables([frame]),
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: pytest.fail(
            "pdfplumber não deve rodar após tabela Camelot válida"
        ),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    client = app.test_client()
    response = client.post(
        "/api/convert/to-csv",
        data=_upload(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["count"] == 1
    assert set(body["files"][0]) == {"name", "size", "download_url"}
    assert client.get(body["files"][0]["download_url"]).status_code == 200
    outputs = _generated_files(app.config["UPLOAD_FOLDER"])
    assert len(outputs) == 1
    with outputs[0].open("r", encoding="utf-8", newline="") as stream:
        values = [row[0] for row in csv.reader(stream)]

    expected = ["cabecalho"]
    expected.extend(
        [
            converter_service._neutralize_csv_field_for_spreadsheet(value)
            for value in payloads
        ]
    )
    assert values == expected
    assert "-10" in values
    assert "-10.5" in values
    assert "-1E2" in values
    assert "'-10+20" in values
    assert "'-1E2+3" in values
    for payload in payloads:
        assert payload not in caplog.text


@pytest.mark.parametrize("blank_page", [False, True])
def test_pdf_without_useful_table_returns_422_without_publication(
    app,
    monkeypatch,
    blank_page,
):
    import camelot
    import pdfplumber

    _patch_prerequisites(monkeypatch)
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: _Tables([]),
    )
    page = _Page(tables=[], text="" if blank_page else "texto sem tabela")
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: _PdfContext([page]),
    )

    response = app.test_client().post(
        "/api/convert/to-csv",
        data=_upload(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert response.get_json() == NO_TABLE_RESPONSE
    assert "download_url" not in response.get_data(as_text=True)
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_all_extractors_failing_returns_503_without_diagnostic_or_success_event(
    app,
    monkeypatch,
    caplog,
):
    import camelot
    import pdfplumber
    from app.routes import converter as converter_routes

    marker = "SYNTHETIC_SECRET_MARKER"
    success_events = []
    _patch_prerequisites(monkeypatch)
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    monkeypatch.setattr(
        converter_routes,
        "record_job_event",
        lambda **kwargs: success_events.append(kwargs),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    response = app.test_client().post(
        "/api/convert/to-csv",
        data=_upload(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == EXTRACTION_RESPONSE
    assert "download_url" not in response.get_data(as_text=True)
    assert "Falha ao extrair conteúdo do PDF." not in response.get_data(
        as_text=True
    )
    assert marker not in caplog.text
    assert success_events == []
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_camelot_failure_with_completed_pdfplumber_never_writes_diagnostic(
    app,
    monkeypatch,
    caplog,
):
    import camelot
    import pdfplumber

    marker = "SYNTHETIC_CAMELOT_MARKER"
    _patch_prerequisites(monkeypatch)
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: _PdfContext([_Page(tables=[])]),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    response = app.test_client().post(
        "/api/convert/to-csv",
        data=_upload(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert response.get_json() == NO_TABLE_RESPONSE
    assert "download_url" not in response.get_data(as_text=True)
    assert "Falha ao extrair conteúdo do PDF." not in response.get_data(
        as_text=True
    )
    assert marker not in caplog.text
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_pdfplumber_failure_after_empty_camelot_returns_controlled_503(
    app,
    monkeypatch,
    caplog,
):
    import camelot
    import pdfplumber

    marker = "SYNTHETIC_PDFPLUMBER_MARKER"
    _patch_prerequisites(monkeypatch)
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: _Tables([]),
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    response = app.test_client().post(
        "/api/convert/to-csv",
        data=_upload(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == EXTRACTION_RESPONSE
    assert "download_url" not in response.get_data(as_text=True)
    assert marker not in caplog.text
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_empty_or_header_only_structured_tables_are_not_publishable(
    tmp_path,
    monkeypatch,
):
    import camelot
    import pdfplumber

    _patch_prerequisites(monkeypatch)
    frames = [pd.DataFrame(), pd.DataFrame([["somente cabeçalho"]])]
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: _Tables(frames),
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: _PdfContext([_Page(tables=[])]),
    )

    with pytest.raises(converter_service.ConverterNoTableError):
        converter_service._pdf_to_csv(
            str(tmp_path / "synthetic.pdf"),
            str(tmp_path / "out"),
        )

    assert not list((tmp_path / "out").glob("*.csv"))


def test_serialization_failure_removes_partial_and_stops_fallback(
    tmp_path,
    monkeypatch,
    caplog,
):
    import camelot
    import pdfplumber

    marker = "SYNTHETIC_WRITE_MARKER"
    _patch_prerequisites(monkeypatch)
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: _Tables(
            [pd.DataFrame([["cabecalho"], ["valor"]])]
        ),
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: pytest.fail(
            "não deve haver fallback após falha de serialização"
        ),
    )

    def fail_after_partial(_self, path, *_args, **_kwargs):
        Path(path).write_text("partial", encoding="utf-8")
        raise OSError(marker)

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail_after_partial)
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    with pytest.raises(converter_service.ConverterExtractionError):
        converter_service._pdf_to_csv(
            str(tmp_path / "synthetic.pdf"),
            str(tmp_path / "out"),
        )

    assert marker not in caplog.text
    assert list((tmp_path / "out").iterdir()) == []


def test_failure_after_useful_table_does_not_publish_partial_result(
    tmp_path,
    monkeypatch,
    caplog,
):
    import camelot
    import pdfplumber

    marker = "SYNTHETIC_LATE_EXTRACTION_MARKER"

    class ExplodingTable:
        @property
        def df(self):
            raise RuntimeError(marker)

    _patch_prerequisites(monkeypatch)
    collection = SimpleNamespace(tables=[
        SimpleNamespace(df=pd.DataFrame([["cabecalho"], ["valor"]])),
        ExplodingTable(),
    ])
    monkeypatch.setattr(
        camelot,
        "read_pdf",
        lambda *_args, **_kwargs: collection,
    )
    monkeypatch.setattr(
        pdfplumber,
        "open",
        lambda *_args, **_kwargs: pytest.fail(
            "não deve haver fallback após extração parcial"
        ),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()

    with pytest.raises(converter_service.ConverterExtractionError):
        converter_service._pdf_to_csv(
            str(tmp_path / "synthetic.pdf"),
            str(tmp_path / "out"),
        )

    assert marker not in caplog.text
    assert list((tmp_path / "out").iterdir()) == []


def test_invalid_csv_from_pdf_is_rejected_and_batch_temp_is_removed(
    app,
    monkeypatch,
):
    from app.routes import converter as converter_routes

    produced = []
    success_events = []

    def fake_pdf_to_csv(_input_path, out_dir):
        output = Path(out_dir) / "synthetic.csv"
        output.write_text(
            "<html><body>synthetic failure</body></html>",
            encoding="utf-8",
        )
        produced.append(output)
        return str(output)

    monkeypatch.setattr(
        converter_service,
        "_pdf_to_csv",
        fake_pdf_to_csv,
    )
    monkeypatch.setattr(
        converter_routes,
        "record_job_event",
        lambda **kwargs: success_events.append(kwargs),
    )

    response = app.test_client().post(
        "/api/convert/to-csv",
        data=_upload(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == INVALID_OUTPUT_RESPONSE
    assert "download_url" not in response.get_data(as_text=True)
    assert produced and all(not path.exists() for path in produced)
    assert success_events == []
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_pdf_csv_does_not_use_text_or_ocr_fallback():
    source = inspect.getsource(converter_service._pdf_to_csv)

    assert "extract_text" not in source
    assert "_try_ocr" not in source
