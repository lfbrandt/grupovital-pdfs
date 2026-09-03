import ast
import inspect
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import camelot_worker, converter_service
from app.services.converter_service import (
    ConverterTimeoutError,
    ConverterToolExecutionError,
)


def _worker_request(
    workdir: Path,
    *,
    flavor: str = "lattice",
    operation: str = "extract",
    pages: str = "2",
    page_hint: int = 2,
    table_area: str | None = "10,90,190,10",
):
    source = workdir / "input.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    options = {"strip_text": "\n", "dpi": 200}
    extractor = "camelot-lattice-region"
    if flavor == "lattice":
        options.update({
            "line_scale": 80,
            "process_background": False,
            "copy_text": ["h", "v"],
            "shift_text": ["l", "t"],
        })
    else:
        extractor = "camelot-stream-smart"
        if table_area is not None:
            options["columns"] = "50,100"
    return {
        "protocol": camelot_worker.PROTOCOL_VERSION,
        "operation": operation,
        "request_id": "a" * 32,
        "input_file": source.name,
        "flavor": flavor,
        "pages": pages,
        "page_hint": page_hint,
        "extractor": extractor,
        "region_prefix": "p0002-test",
        "region_index_width": 3,
        "table_area": table_area,
        "options": options,
    }


def _response_for_request(request, *, tables=None):
    return {
        "protocol": request["protocol"],
        "operation": request["operation"],
        "request_id": request["request_id"],
        "flavor": request["flavor"],
        "pages": request["pages"],
        "page_hint": request["page_hint"],
        "extractor": request["extractor"],
        "region_prefix": request["region_prefix"],
        "region_index_width": request["region_index_width"],
        "table_area": request["table_area"],
        "tables": [] if tables is None else tables,
    }


def _table_payload(
    *,
    page=2,
    bbox=None,
    rows=None,
    report=None,
):
    rows = rows or [["Código", "Valor"], ["001", "10"]]
    return {
        "page": page,
        "bbox": bbox or [10.0, 10.0, 190.0, 90.0],
        "row_count": len(rows),
        "column_count": len(rows[0]),
        "rows": rows,
        "report": report or {
            "accuracy": 99.0,
            "whitespace": 1.0,
            "order": 1,
            "page": page,
        },
    }


def _install_parent_runner(monkeypatch, response_factory):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        request_rel = command[command.index("--request") + 1]
        result_rel = command[command.index("--result") + 1]
        request = json.loads(
            (Path(kwargs["cwd"]) / request_rel).read_text(encoding="utf-8")
        )
        response = response_factory(request)
        if response is not None:
            (Path(kwargs["cwd"]) / result_rel).write_text(
                json.dumps(response, ensure_ascii=False),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(converter_service, "run_in_sandbox", fake_run)
    return calls


class _FakeCamelotTable:
    def __init__(self, rows, *, page=2, bbox=(10, 10, 190, 90)):
        self.df = pd.DataFrame(rows)
        self.page = page
        self._bbox = bbox
        self.parsing_report = {
            "accuracy": 98.5,
            "whitespace": 1.5,
            "order": 1,
            "page": page,
        }


@pytest.mark.parametrize("flavor", ["lattice", "stream"])
def test_worker_accepts_allowed_flavors(monkeypatch, tmp_path, flavor):
    request = _worker_request(tmp_path, flavor=flavor)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    captured = {}

    def fake_read_pdf(path, **kwargs):
        captured["path"] = path
        captured["kwargs"] = kwargs
        return [_FakeCamelotTable([["Código", "Valor"], ["001", "10"]])]

    monkeypatch.setitem(
        sys.modules,
        "camelot",
        SimpleNamespace(read_pdf=fake_read_pdf),
    )
    camelot_worker.execute_request(
        request_path.name,
        "result.json",
        workdir=tmp_path,
    )

    response = json.loads((tmp_path / "result.json").read_text("utf-8"))
    assert captured["path"] == str(tmp_path / "input.pdf")
    assert captured["kwargs"]["flavor"] == flavor
    assert captured["kwargs"]["pages"] == "2"
    assert response["tables"][0]["rows"] == [
        ["Código", "Valor"],
        ["001", "10"],
    ]
    assert response["tables"][0]["bbox"] == [10.0, 10.0, 190.0, 90.0]
    assert response["tables"][0]["report"]["accuracy"] == 98.5
    if flavor == "stream":
        assert captured["kwargs"]["columns"] == ["50,100"]
    else:
        assert captured["kwargs"]["line_scale"] == 80


def test_worker_rejects_unknown_operation_and_flavor(tmp_path):
    unknown_operation = _worker_request(
        tmp_path,
        operation="inspect",
    )
    with pytest.raises(camelot_worker.WorkerRequestError):
        camelot_worker.validate_request(unknown_operation, tmp_path)

    unknown_flavor = _worker_request(tmp_path)
    unknown_flavor["flavor"] = "hybrid"
    with pytest.raises(camelot_worker.WorkerRequestError):
        camelot_worker.validate_request(unknown_flavor, tmp_path)


def test_worker_rejects_unknown_fields_and_options(tmp_path):
    request = _worker_request(tmp_path)
    request["unexpected"] = True
    with pytest.raises(camelot_worker.WorkerRequestError):
        camelot_worker.validate_request(request, tmp_path)

    request = _worker_request(tmp_path)
    request["options"]["password"] = "segredo"
    with pytest.raises(camelot_worker.WorkerRequestError):
        camelot_worker.validate_request(request, tmp_path)


def test_worker_page_limit_matches_project_pdf_limit(tmp_path):
    pages = ",".join(str(page) for page in range(1, 801))
    request = _worker_request(
        tmp_path,
        pages=pages,
        page_hint=800,
        table_area=None,
    )

    validated = camelot_worker.validate_request(request, tmp_path)

    assert len(validated["allowed_pages"]) == 800
    assert max(validated["allowed_pages"]) == 800


def test_worker_publishes_result_atomically(monkeypatch, tmp_path):
    request = _worker_request(tmp_path)
    (tmp_path / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        sys.modules,
        "camelot",
        SimpleNamespace(
            read_pdf=lambda *_args, **_kwargs: [
                _FakeCamelotTable([["A"], ["B"]])
            ]
        ),
    )

    camelot_worker.execute_request(
        "request.json",
        "result.json",
        workdir=tmp_path,
    )

    assert (tmp_path / "result.json").is_file()
    assert not list(tmp_path.glob(".camelot-result-*.tmp"))


def test_pdf_xlsx_has_no_direct_camelot_call_in_web_process():
    source = Path(converter_service.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct_calls = []
    containing_function = None

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            nonlocal containing_function
            previous = containing_function
            containing_function = node.name
            self.generic_visit(node)
            containing_function = previous

        def visit_Call(self, node):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "read_pdf"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "camelot"
            ):
                direct_calls.append(containing_function)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert direct_calls == ["_pdf_to_csv"]
    pdf_csv_source = inspect.getsource(converter_service._pdf_to_csv)
    assert 'for flavor in ("lattice", "stream")' in pdf_csv_source


def test_worker_protocol_avoids_arbitrary_deserialization():
    source = Path(camelot_worker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"eval", "exec"}
    }
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert forbidden_calls == set()
    assert "pickle" not in imports


def test_parent_uses_sys_executable_list_and_shared_sandbox(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")
    calls = _install_parent_runner(
        monkeypatch,
        lambda request: _response_for_request(request),
    )

    result = converter_service._run_camelot_worker(
        str(source),
        flavor="lattice",
        pages="1",
        page_hint=1,
        extractor="camelot-lattice-global",
        region_prefix="global",
        region_index_width=3,
    )

    assert result == []
    command, kwargs = calls[0]
    assert isinstance(command, list)
    assert command[0] == sys.executable
    assert kwargs["cwd"] == str(tmp_path)
    assert "shell" not in kwargs
    assert kwargs["timeout"] > 0
    assert not list(tmp_path.glob(".camelot-worker-*"))


def test_worker_environment_does_not_forward_proxy_or_credentials(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.invalid")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "segredo")

    environment = converter_service._camelot_worker_env(str(tmp_path))

    assert "HTTP_PROXY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["TEMP"] == str(tmp_path)
    assert environment["TMP"] == str(tmp_path)


def test_timeout_is_limited_by_remaining_total_budget(monkeypatch, tmp_path):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")
    calls = _install_parent_runner(
        monkeypatch,
        lambda request: _response_for_request(request),
    )

    class Runtime:
        def remaining(self, _stage):
            return 7.0

    token = converter_service._ACTIVE_CONVERTER_RUNTIME.set(Runtime())
    try:
        converter_service._run_camelot_worker(
            str(source),
            flavor="stream",
            pages="1",
            page_hint=1,
            extractor="camelot-stream-global",
            region_prefix="stream",
            region_index_width=3,
        )
    finally:
        converter_service._ACTIVE_CONVERTER_RUNTIME.reset(token)

    assert calls[0][1]["timeout"] == pytest.approx(5.0)


def test_cleanup_margin_exhaustion_is_not_swallowed_by_fallback(
    monkeypatch,
):
    monkeypatch.setattr(
        converter_service,
        "_fallback_reasons_by_page",
        lambda *_args, **_kwargs: {1: "missing_candidate"},
    )
    monkeypatch.setattr(
        converter_service,
        "_rescue_with_stream",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(
                converter_service._ConverterDeadlineReservedError(
                    "margem reservada"
                )
            )
        ),
    )

    with pytest.raises(ConverterTimeoutError):
        converter_service._run_pdf_xlsx_fallback_pass(
            "input.pdf",
            [1],
            [],
            allow_stream=True,
        )


def test_subprocess_does_not_start_after_deadline(monkeypatch, tmp_path):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")
    called = False

    def forbidden_runner(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("worker não deveria iniciar")

    class ExpiredRuntime:
        def remaining(self, _stage):
            raise ConverterTimeoutError("deadline")

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        forbidden_runner,
    )
    token = converter_service._ACTIVE_CONVERTER_RUNTIME.set(
        ExpiredRuntime()
    )
    try:
        with pytest.raises(ConverterTimeoutError):
            converter_service._run_camelot_worker(
                str(source),
                flavor="lattice",
                pages="1",
                page_hint=1,
                extractor="camelot-lattice-global",
                region_prefix="global",
                region_index_width=3,
            )
    finally:
        converter_service._ACTIVE_CONVERTER_RUNTIME.reset(token)

    assert called is False
    assert not list(tmp_path.glob(".camelot-worker-*"))


def test_timeout_is_controlled_and_removes_partial_result(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")

    def timeout_runner(command, **kwargs):
        result_rel = command[command.index("--result") + 1]
        (Path(kwargs["cwd"]) / result_rel).write_text(
            '{"partial":',
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        timeout_runner,
    )
    with pytest.raises(ConverterTimeoutError):
        converter_service._run_camelot_worker(
            str(source),
            flavor="lattice",
            pages="1",
            page_hint=1,
            extractor="camelot-lattice-global",
            region_prefix="global",
            region_index_width=3,
        )

    assert not list(tmp_path.glob(".camelot-worker-*"))


def test_worker_crash_does_not_publish_or_leave_attempt(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")

    def crash_runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            camelot_worker.EXIT_EXTRACTION_FAILED,
            stdout="",
            stderr="conteúdo sensível que não deve ser registrado",
        )

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        crash_runner,
    )
    with pytest.raises(ConverterToolExecutionError):
        converter_service._run_camelot_worker(
            str(source),
            flavor="lattice",
            pages="1",
            page_hint=1,
            extractor="camelot-lattice-global",
            region_prefix="global",
            region_index_width=3,
        )

    assert not list(tmp_path.glob(".camelot-worker-*"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda response: "{",
        lambda response: {**response, "protocol": 999},
        lambda response: {**response, "pages": "3"},
        lambda response: {
            **response,
            "tables": [_table_payload(page=3)],
        },
    ],
)
def test_parent_rejects_invalid_json_protocol_or_provenance(
    monkeypatch,
    tmp_path,
    mutate,
):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")

    def runner(command, **kwargs):
        request_rel = command[command.index("--request") + 1]
        result_rel = command[command.index("--result") + 1]
        request = json.loads(
            (Path(kwargs["cwd"]) / request_rel).read_text("utf-8")
        )
        result = mutate(_response_for_request(request))
        path = Path(kwargs["cwd"]) / result_rel
        if isinstance(result, str):
            path.write_text(result, encoding="utf-8")
        else:
            path.write_text(json.dumps(result), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(converter_service, "run_in_sandbox", runner)
    with pytest.raises(ConverterToolExecutionError):
        converter_service._run_camelot_worker(
            str(source),
            flavor="lattice",
            pages="2",
            page_hint=2,
            extractor="camelot-lattice-global",
            region_prefix="global",
            region_index_width=3,
        )

    assert not list(tmp_path.glob(".camelot-worker-*"))


def test_parent_rejects_excessive_result_file(tmp_path):
    request = _worker_request(tmp_path)
    result = tmp_path / "oversized.json"
    with result.open("wb") as stream:
        stream.seek(camelot_worker.MAX_RESULT_BYTES)
        stream.write(b"x")

    with pytest.raises(ConverterToolExecutionError):
        converter_service._validate_camelot_worker_response(
            str(result),
            str(tmp_path),
            request,
        )


@pytest.mark.parametrize(
    "table",
    [
        {
            **_table_payload(),
            "row_count": camelot_worker.MAX_TOTAL_ROWS + 1,
            "rows": [],
        },
        {
            **_table_payload(),
            "column_count": camelot_worker.MAX_COLUMNS + 1,
        },
        {
            **_table_payload(),
            "row_count": 10_000,
            "column_count": 256,
            "rows": [],
        },
    ],
)
def test_parent_rejects_excessive_table_dimensions(tmp_path, table):
    request = _worker_request(tmp_path)
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            _response_for_request(request, tables=[table]),
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConverterToolExecutionError):
        converter_service._validate_camelot_worker_response(
            str(result),
            str(tmp_path),
            request,
        )


def test_parent_rejects_too_many_tables(tmp_path):
    request = _worker_request(tmp_path)
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps(
            _response_for_request(
                request,
                tables=[
                    _table_payload()
                    for _index in range(camelot_worker.MAX_TABLES + 1)
                ],
            ),
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConverterToolExecutionError):
        converter_service._validate_camelot_worker_response(
            str(result),
            str(tmp_path),
            request,
        )


def test_parent_rejects_result_outside_workdir(tmp_path):
    workdir = tmp_path / "job"
    workdir.mkdir()
    request = _worker_request(workdir)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(_response_for_request(request)),
        encoding="utf-8",
    )

    with pytest.raises(ConverterToolExecutionError):
        converter_service._validate_camelot_worker_response(
            str(outside),
            str(workdir),
            request,
        )


def test_parent_rejects_symlink_result_when_supported(tmp_path):
    request = _worker_request(tmp_path)
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps(_response_for_request(request)),
        encoding="utf-8",
    )
    link = tmp_path / "result.json"
    try:
        os.symlink(target, link)
    except (NotImplementedError, OSError):
        pytest.skip("symlink não suportado neste ambiente")

    with pytest.raises(ConverterToolExecutionError):
        converter_service._validate_camelot_worker_response(
            str(link),
            str(tmp_path),
            request,
        )


def test_empty_response_returns_no_candidate(monkeypatch, tmp_path):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")
    _install_parent_runner(
        monkeypatch,
        lambda request: _response_for_request(request),
    )

    candidates = converter_service._run_camelot_worker(
        str(source),
        flavor="stream",
        pages="1",
        page_hint=1,
        extractor="camelot-stream-global",
        region_prefix="stream",
        region_index_width=3,
    )

    assert candidates == []


def test_parent_preserves_rows_columns_page_bbox_region_and_report(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")
    rows = [
        ["Código", "Valor", "Ativo"],
        ["001", 10.5, True],
    ]
    _install_parent_runner(
        monkeypatch,
        lambda request: _response_for_request(
            request,
            tables=[
                _table_payload(
                    page=2,
                    bbox=[11.0, 12.0, 180.0, 88.0],
                    rows=rows,
                    report={"accuracy": 97.0, "page": 2},
                )
            ],
        ),
    )

    candidates = converter_service._run_camelot_worker(
        str(source),
        flavor="lattice",
        pages="2",
        page_hint=2,
        extractor="camelot-lattice-region",
        region_prefix="p0002-r",
        region_index_width=3,
        table_area="10,90,190,10",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.page_number == 2
    assert candidate.region_id == "p0002-r-001"
    assert candidate.bbox == (11.0, 12.0, 180.0, 88.0)
    assert candidate.structural_report == {"accuracy": 97.0, "page": 2}
    assert candidate.dataframe.shape == (2, 3)
    assert candidate.dataframe.values.tolist() == rows


def test_logs_do_not_include_cells_or_worker_output(
    monkeypatch,
    tmp_path,
    caplog,
):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF")
    sensitive = "CPF-123.456.789-00"

    def response(request):
        return _response_for_request(
            request,
            tables=[_table_payload(rows=[["CPF"], [sensitive]])],
        )

    _install_parent_runner(monkeypatch, response)
    with caplog.at_level(logging.INFO):
        converter_service._run_camelot_worker(
            str(source),
            flavor="stream",
            pages="2",
            page_hint=2,
            extractor="camelot-stream-global",
            region_prefix="stream",
            region_index_width=3,
        )

    assert sensitive not in caplog.text
    assert "CPF" not in caplog.text
