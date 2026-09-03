import io
import os
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest
from PyPDF2 import PdfWriter

from app import create_app
from app.services import converter_service
from app.services.converter_service import (
    ConverterTimeoutError,
    ConverterToolExecutionError,
    ConverterToolUnavailableError,
)
from app.utils.security import OUTPUT_OWNER_SESSION_KEY


TIMEOUT_PAYLOAD = {
    "error": (
        "A conversão excedeu o tempo máximo permitido. "
        "Tente novamente com menos arquivos."
    )
}


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    buffer = io.BytesIO()
    workbook = Workbook()
    workbook.active["A1"] = "valor"
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _write_structural_output(path: Path, target: str) -> None:
    if target == "pdf":
        path.write_bytes(_pdf_bytes())
        return
    if target == "csv":
        path.write_text("codigo,valor\n001,10\n", encoding="utf-8")
        return
    if target == "docx":
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr(
                "word/document.xml",
                (
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body/></w:document>'
                ),
            )
        return

    path.write_bytes(_xlsx_bytes())
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
        CONVERTER_MAX_RUNTIME_SEC=300,
    )
    return app


def _uploads(count=1, *, filename="entrada.pdf", payload=None):
    data = payload if payload is not None else _pdf_bytes()
    return {
        "files[]": [
            (io.BytesIO(data), filename)
            for _ in range(count)
        ]
    }


def _generated_files(upload_folder):
    generated = Path(upload_folder) / "generated"
    if not generated.exists():
        return []
    return sorted(path for path in generated.rglob("*") if path.is_file())


def _seed_preserved_files(app, client):
    owner = "c" * 32
    previous = (
        Path(app.config["UPLOAD_FOLDER"])
        / "generated"
        / owner
        / ("1" * 32)
        / "anterior.pdf"
    )
    previous.parent.mkdir(parents=True)
    previous.write_bytes(_pdf_bytes())
    upload = Path(app.config["UPLOAD_FOLDER"]) / "upload-anterior.pdf"
    upload.write_bytes(_pdf_bytes())
    with client.session_transaction() as session:
        session[OUTPUT_OWNER_SESSION_KEY] = owner
    return previous, upload


def _install_fake_batch_converter(monkeypatch, converter_routes):
    calls = {"count": 0}

    def fake_convert(_upload, target, out_dir):
        calls["count"] += 1
        output = Path(out_dir) / f"resultado-{calls['count']}.{target}"
        _write_structural_output(output, target)
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    return calls


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    decoded = unquote(parsed.path)
    if os.name == "nt":
        decoded = decoded.lstrip("/")
    return Path(decoded)


def test_sandbox_uses_list_shell_false_timeout_and_preserves_returncode(
    monkeypatch, tmp_path
):
    from app.services import sandbox

    captured = {}

    class FakeProcess:
        pid = 12345
        returncode = 9

        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            return "stdout-controlado", "stderr-controlado"

        def poll(self):
            return self.returncode

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(sandbox.subprocess, "Popen", fake_popen)
    result = sandbox.run_in_sandbox(
        [sys.executable, "--version"],
        cwd=str(tmp_path),
        timeout=7,
        cpu_seconds=3,
        mem_mb=64,
    )

    assert captured["cmd"] == [sys.executable, "--version"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["timeout"] == 7
    assert result.returncode == 9
    assert result.stdout == "stdout-controlado"
    assert result.stderr == "stderr-controlado"
    if os.name == "posix":
        assert captured["kwargs"]["start_new_session"] is True
        assert callable(captured["kwargs"]["preexec_fn"])
    else:
        assert captured["kwargs"]["creationflags"] == getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0,
        )
        assert captured["kwargs"]["preexec_fn"] is None


def test_sandbox_rejects_string_command_and_nonpositive_timeout():
    from app.services.sandbox import run_in_sandbox

    with pytest.raises(TypeError):
        run_in_sandbox("echo inseguro")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        run_in_sandbox([sys.executable], timeout=0)


def test_sandbox_timeout_is_preemptive_for_direct_process(tmp_path):
    from app.services.sandbox import run_in_sandbox

    with pytest.raises(subprocess.TimeoutExpired):
        run_in_sandbox(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(5)",
            ],
            cwd=str(tmp_path),
            timeout=0.1,
            output_limit_chars=128,
        )


def test_libreoffice_uses_executor_isolated_profile_and_remaining_budget(
    monkeypatch, tmp_path
):
    calls = []
    clock = {"now": 10.0}
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        converter_service,
        "_soffice_bin",
        lambda: "soffice-test",
    )

    def fake_sandbox(cmd, **kwargs):
        profile_arg = next(
            item for item in cmd
            if item.startswith("-env:UserInstallation=")
        )
        profile_uri = profile_arg.split("=", 1)[1]
        profile_path = _path_from_file_uri(profile_uri)
        assert profile_path.exists()
        calls.append((list(cmd), dict(kwargs), profile_path))
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        source = Path(cmd[-1])
        (outdir / f"{source.stem}.pdf").write_bytes(_pdf_bytes())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        fake_sandbox,
    )
    source = tmp_path / "entrada.docx"
    source.write_bytes(b"entrada")
    second_source = tmp_path / "entrada-2.docx"
    second_source.write_bytes(b"entrada")
    with converter_service.converter_job_runtime(30):
        result = converter_service._lo_convert(
            str(source),
            str(tmp_path),
            "pdf",
        )
        second_result = converter_service._lo_convert(
            str(second_source),
            str(tmp_path),
            "pdf",
        )

    assert Path(result).exists()
    assert Path(second_result).exists()
    assert len(calls) == 2
    assert calls[0][2] != calls[1][2]
    command, kwargs, profile_path = calls[0]
    assert "--headless" in command
    assert "--safe-mode" in command
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert 0 < kwargs["timeout"] <= 30
    assert kwargs["mem_mb"] == 1024
    assert all(not call[2].exists() for call in calls)


def test_concurrent_libreoffice_jobs_are_isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(
        converter_service,
        "_soffice_bin",
        lambda: "soffice-test",
    )
    barrier = threading.Barrier(2)
    captured = []
    captured_lock = threading.Lock()

    def fake_sandbox(cmd, **kwargs):
        profile_arg = next(
            item for item in cmd
            if item.startswith("-env:UserInstallation=")
        )
        profile_path = _path_from_file_uri(profile_arg.split("=", 1)[1])
        outdir = Path(cmd[cmd.index("--outdir") + 1]).resolve()
        source = Path(cmd[-1]).resolve()
        assert profile_path.exists()
        assert Path(kwargs["cwd"]).resolve() == outdir
        barrier.wait(timeout=5)
        output = outdir / f"{source.stem}.pdf"
        output.write_bytes(_pdf_bytes())
        with captured_lock:
            captured.append({
                "cwd": Path(kwargs["cwd"]).resolve(),
                "outdir": outdir,
                "profile": profile_path,
                "source": source,
                "output": output,
            })
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        fake_sandbox,
    )

    def convert(job_spec):
        session_name, job_name = job_spec
        job_dir = tmp_path / session_name / job_name
        job_dir.mkdir(parents=True)
        source = job_dir / f"{job_name}.docx"
        source.write_bytes(b"entrada")
        with converter_service.converter_job_runtime(30):
            return Path(converter_service._lo_convert(
                str(source),
                str(job_dir),
                "pdf",
            )).resolve()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            convert,
            (("session-a", "job-a"), ("session-b", "job-b")),
        ))

    assert len(captured) == 2
    assert len({item["cwd"] for item in captured}) == 2
    assert len({item["outdir"] for item in captured}) == 2
    assert len({item["profile"] for item in captured}) == 2
    assert len({result.name for result in results}) == 2
    assert all(result.exists() for result in results)
    assert all(result.parent.name in {"job-a", "job-b"} for result in results)
    assert {result.parent.parent.name for result in results} == {
        "session-a",
        "session-b",
    }
    assert all(item["output"].parent == item["source"].parent for item in captured)
    assert all(not item["profile"].exists() for item in captured)
    assert not list(tmp_path.rglob(".lo-profile-*"))


def test_libreoffice_timeout_and_nonzero_are_controlled_and_clean_profile(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        converter_service,
        "_soffice_bin",
        lambda: "soffice-test",
    )
    source = tmp_path / "entrada.docx"
    source.write_bytes(b"entrada")
    profiles = []

    def timeout_sandbox(cmd, **_kwargs):
        profile_arg = next(
            item for item in cmd
            if item.startswith("-env:UserInstallation=")
        )
        profiles.append(_path_from_file_uri(profile_arg.split("=", 1)[1]))
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        timeout_sandbox,
    )
    with pytest.raises(ConverterTimeoutError):
        converter_service._lo_convert(
            str(source),
            str(tmp_path),
            "pdf",
        )
    assert profiles and all(not path.exists() for path in profiles)

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=7,
            stdout="nome-secreto",
            stderr="caminho-secreto",
        ),
    )
    with pytest.raises(ConverterToolExecutionError):
        converter_service._lo_convert(
            str(source),
            str(tmp_path),
            "pdf",
        )


def test_libreoffice_unavailable_is_controlled(monkeypatch, tmp_path):
    def unavailable():
        raise ConverterToolUnavailableError("detalhe interno")

    monkeypatch.setattr(converter_service, "_soffice_bin", unavailable)
    source = tmp_path / "entrada.docx"
    source.write_bytes(b"entrada")

    with pytest.raises(ConverterToolUnavailableError):
        converter_service._lo_convert(
            str(source),
            str(tmp_path),
            "pdf",
        )


def test_healthcheck_uses_executor_short_timeout_and_sanitized_route(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    monkeypatch.setattr(
        converter_routes,
        "libreoffice_healthcheck",
        lambda timeout: "LibreOffice 24.2",
    )
    response = app.test_client().get("/api/convert/health")
    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "lo": "LibreOffice 24.2",
    }

    monkeypatch.setattr(
        converter_routes,
        "libreoffice_healthcheck",
        lambda timeout: (_ for _ in ()).throw(
            ConverterToolExecutionError(
                r"C:\segredo\soffice.exe stderr sigiloso"
            )
        ),
    )
    response = app.test_client().get("/api/convert/health")
    assert response.status_code == 503
    body = response.get_data(as_text=True)
    assert response.get_json() == {
        "ok": False,
        "error": "Não foi possível verificar o LibreOffice.",
    }
    assert "segredo" not in body
    assert "stderr" not in body


def test_libreoffice_healthcheck_uses_executor_and_hides_extra_output(
    monkeypatch
):
    captured = {}
    monkeypatch.setattr(
        converter_service,
        "_soffice_bin",
        lambda: "soffice-test",
    )

    def fake_sandbox(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "LibreOffice 24.2.7.2\n"
                r"C:\dados\sigilosos\perfil"
            ),
            stderr="stderr sigiloso",
        )

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        fake_sandbox,
    )

    version = converter_service.libreoffice_healthcheck(99)

    assert version == "LibreOffice 24.2.7.2"
    assert "--version" in captured["cmd"]
    assert captured["kwargs"]["timeout"] == 5
    assert captured["kwargs"]["output_limit_chars"] == 512
    assert captured["kwargs"]["cwd"]
    assert not Path(captured["kwargs"]["cwd"]).exists()


def test_libreoffice_missing_output_is_controlled(monkeypatch, tmp_path):
    monkeypatch.setattr(
        converter_service,
        "_soffice_bin",
        lambda: "soffice-test",
    )
    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    source = tmp_path / "entrada.docx"
    source.write_bytes(b"entrada")

    with pytest.raises(ConverterToolExecutionError):
        converter_service._lo_convert(
            str(source),
            str(tmp_path),
            "pdf",
        )


def test_converter_ocr_uses_executor_budget_and_cleans_output(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OCR_ON_PDF_TO_XLSX", "1")
    monkeypatch.setenv("OCR_TIMEOUT", "120")
    monkeypatch.setenv("OCR_JOBS", "99")
    monkeypatch.setattr(
        converter_service.shutil,
        "which",
        lambda _name: "ocrmypdf-test",
    )
    monkeypatch.setattr(
        converter_service,
        "enforce_pdf_page_limit",
        lambda *_args, **_kwargs: 1,
    )
    captured = {}

    def fake_sandbox(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        Path(cmd[-1]).write_bytes(_pdf_bytes())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        fake_sandbox,
    )
    source = tmp_path / "entrada.pdf"
    source.write_bytes(_pdf_bytes())
    with converter_service.converter_job_runtime(20):
        output = converter_service._try_ocr(str(source))
        assert Path(output).exists()
        assert Path(output).parent == tmp_path
    assert not Path(output).exists()
    assert 0 < captured["kwargs"]["timeout"] <= 20
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    jobs_index = captured["cmd"].index("--jobs")
    assert captured["cmd"][jobs_index + 1] == "4"


@pytest.mark.parametrize("failure", ["timeout", "invalid", "nonzero"])
def test_converter_ocr_failures_are_controlled_and_cleaned(
    monkeypatch, tmp_path, failure
):
    monkeypatch.setenv("OCR_ON_PDF_TO_XLSX", "1")
    monkeypatch.setattr(
        converter_service.shutil,
        "which",
        lambda _name: "ocrmypdf-test",
    )
    monkeypatch.setattr(
        converter_service,
        "enforce_pdf_page_limit",
        lambda *_args, **_kwargs: 1,
    )
    outputs = []

    def fake_sandbox(cmd, **_kwargs):
        outputs.append(Path(cmd[-1]))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1)
        if failure == "invalid":
            outputs[-1].write_bytes(b"invalid-pdf")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=2, stdout="", stderr="")

    monkeypatch.setattr(
        converter_service,
        "run_in_sandbox",
        fake_sandbox,
    )
    source = tmp_path / "entrada.pdf"
    source.write_bytes(_pdf_bytes())
    expected = (
        ConverterTimeoutError
        if failure == "timeout"
        else ConverterToolExecutionError
    )
    with pytest.raises(expected):
        with converter_service.converter_job_runtime(20):
            converter_service._try_ocr(str(source))
    assert outputs and all(not path.exists() for path in outputs)


def test_converter_ocr_unavailable_is_controlled(monkeypatch, tmp_path):
    monkeypatch.setenv("OCR_ON_PDF_TO_XLSX", "1")
    monkeypatch.setattr(
        converter_service.shutil,
        "which",
        lambda _name: None,
    )
    monkeypatch.setattr(
        converter_service.importlib.util,
        "find_spec",
        lambda _name: None,
    )
    source = tmp_path / "entrada.pdf"
    source.write_bytes(_pdf_bytes())

    with pytest.raises(ConverterToolUnavailableError):
        converter_service._try_ocr(str(source))


@pytest.mark.parametrize("invalid", [None, "inválido", 0, -1])
def test_converter_runtime_config_uses_safe_default(monkeypatch, invalid):
    monkeypatch.delenv("CONVERTER_MAX_RUNTIME_SEC", raising=False)
    if invalid is not None:
        monkeypatch.setenv("CONVERTER_MAX_RUNTIME_SEC", str(invalid))
    from app.utils.limits import get_converter_max_runtime_sec

    assert get_converter_max_runtime_sec() == 300


def test_converter_runtime_config_is_loaded_into_app(monkeypatch):
    monkeypatch.setenv("CONVERTER_MAX_RUNTIME_SEC", "47")

    configured_app = create_app()

    assert configured_app.config["CONVERTER_MAX_RUNTIME_SEC"] == 47


def test_budget_is_not_reset_between_files_and_remaining_decreases(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    app.config["CONVERTER_MAX_RUNTIME_SEC"] = 100
    clock = {"now": 0.0}
    timeouts = []
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )

    def fake_convert(_upload, target, out_dir):
        timeouts.append(
            converter_service._effective_converter_timeout(
                100,
                "test-tool",
            )
        )
        output = Path(out_dir) / f"resultado.{target}"
        _write_structural_output(output, target)
        clock["now"] += 20
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert len(timeouts) == 2
    assert timeouts[0] == 100
    assert timeouts[1] == 80


def test_regular_batch_creates_only_one_deadline(app, monkeypatch):
    from app.routes import converter as converter_routes

    original_runtime = converter_routes.converter_job_runtime
    contexts = {"count": 0}

    @contextmanager
    def counting_runtime(seconds):
        contexts["count"] += 1
        with original_runtime(seconds) as runtime:
            yield runtime

    def fake_convert(_upload, target, out_dir):
        output = Path(out_dir) / f"resultado.{target}"
        _write_structural_output(output, target)
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "converter_job_runtime",
        counting_runtime,
    )
    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert contexts["count"] == 1


def test_budget_is_not_reset_between_fallback_stages(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )

    with converter_service.converter_job_runtime(100):
        first = converter_service._effective_converter_timeout(
            90,
            "primary",
        )
        clock["now"] = 65.0
        fallback = converter_service._effective_converter_timeout(
            90,
            "fallback",
        )

    assert first == 90
    assert fallback == 35


def test_expired_budget_prevents_next_file_and_publishes_nothing(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    app.config["CONVERTER_MAX_RUNTIME_SEC"] = 30
    clock = {"now": 0.0}
    calls = {"count": 0}
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )

    def fake_convert(_upload, target, out_dir):
        calls["count"] += 1
        output = Path(out_dir) / f"resultado.{target}"
        _write_structural_output(output, target)
        clock["now"] = 31.0
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == TIMEOUT_PAYLOAD
    assert calls["count"] == 1
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_expired_budget_does_not_publish_and_preserves_previous_job(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    owner = "a" * 32
    previous = (
        Path(app.config["UPLOAD_FOLDER"])
        / "generated"
        / owner
        / ("1" * 32)
        / "anterior.pdf"
    )
    previous.parent.mkdir(parents=True)
    previous.write_bytes(_pdf_bytes())
    client = app.test_client()
    with client.session_transaction() as session:
        session[OUTPUT_OWNER_SESSION_KEY] = owner

    clock = {"now": 0.0}
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )

    def fake_convert(_upload, target, out_dir):
        output = Path(out_dir) / f"resultado.{target}"
        _write_structural_output(output, target)
        clock["now"] = 301.0
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    response = client.post(
        "/api/convert/to-pdf",
        data=_uploads(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert previous.exists()
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == [previous]


def test_expiration_during_publication_removes_current_job(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    clock = {"now": 0.0}
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )

    def fake_convert(_upload, target, out_dir):
        output = Path(out_dir) / f"resultado.{target}"
        _write_structural_output(output, target)
        return str(output)

    original_move = converter_routes._move_into_uploads

    def move_then_expire(*args, **kwargs):
        result = original_move(*args, **kwargs)
        clock["now"] = 301.0
        return result

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    monkeypatch.setattr(
        converter_routes,
        "_move_into_uploads",
        move_then_expire,
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == TIMEOUT_PAYLOAD
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


def test_failure_after_first_atomic_replace_cleans_only_current_job(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    client = app.test_client()
    previous, upload = _seed_preserved_files(app, client)
    _install_fake_batch_converter(monkeypatch, converter_routes)
    original_move = converter_routes._xdev_safe_move
    original_replace = converter_routes.os.replace
    moves = {"count": 0}
    replaces = []

    def tracking_replace(src, dst):
        result = original_replace(src, dst)
        replaces.append((src, dst))
        return result

    monkeypatch.setattr(
        converter_routes.os,
        "replace",
        tracking_replace,
    )

    def fail_after_first_replace(src, dst):
        moves["count"] += 1
        if moves["count"] == 1:
            result = original_move(src, dst)
            assert Path(result).exists()
            assert replaces == [(src, dst)]
            return result
        raise RuntimeError("falha injetada depois do primeiro replace")

    monkeypatch.setattr(
        converter_routes,
        "_xdev_safe_move",
        fail_after_first_replace,
    )
    response = client.post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert moves["count"] == 2
    assert len(replaces) == 1
    assert previous.exists()
    assert upload.exists()
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == [previous]
    assert "falha injetada" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("stage", "failure_kind"),
    [
        ("during-publication", "runtime"),
        ("during-publication", "timeout"),
        ("during-metadata", "runtime"),
        ("during-metadata", "timeout"),
        ("before-response", "runtime"),
        ("before-response", "timeout"),
    ],
)
def test_late_failure_or_timeout_rolls_back_current_batch_only(
    app,
    monkeypatch,
    stage,
    failure_kind,
):
    from app.routes import converter as converter_routes

    client = app.test_client()
    previous, upload = _seed_preserved_files(app, client)
    _install_fake_batch_converter(monkeypatch, converter_routes)

    def injected_error():
        if failure_kind == "timeout":
            return ConverterTimeoutError("deadline interno")
        return RuntimeError("falha interna injetada")

    if stage == "during-publication":
        original_move = converter_routes._move_into_uploads
        moves = {"count": 0}

        def move_then_fail(*args, **kwargs):
            result = original_move(*args, **kwargs)
            moves["count"] += 1
            if moves["count"] == 1:
                raise injected_error()
            return result

        monkeypatch.setattr(
            converter_routes,
            "_move_into_uploads",
            move_then_fail,
        )
    elif stage == "during-metadata":
        original_info = converter_routes._file_info_for_response

        def metadata_failure(path):
            original_info(path)
            raise injected_error()

        monkeypatch.setattr(
            converter_routes,
            "_file_info_for_response",
            metadata_failure,
        )
    else:
        original_jsonify = converter_routes.jsonify

        def response_failure(*args, **kwargs):
            payload = args[0] if args else kwargs
            if isinstance(payload, dict) and "files" in payload:
                raise injected_error()
            return original_jsonify(*args, **kwargs)

        monkeypatch.setattr(
            converter_routes,
            "jsonify",
            response_failure,
        )

    response = client.post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    if failure_kind == "timeout":
        assert response.get_json() == TIMEOUT_PAYLOAD
    assert previous.exists()
    assert upload.exists()
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == [previous]
    body = response.get_data(as_text=True)
    assert "deadline interno" not in body
    assert "falha interna injetada" not in body


@pytest.mark.parametrize(
    "checkpoint",
    [
        "after-all-published",
        "after-all-metadata",
        "after-response-built",
    ],
)
def test_timeout_at_final_publication_checkpoints_is_compensated(
    app,
    monkeypatch,
    checkpoint,
):
    from app.routes import converter as converter_routes

    client = app.test_client()
    previous, upload = _seed_preserved_files(app, client)
    _install_fake_batch_converter(monkeypatch, converter_routes)
    clock = {"now": 0.0}
    monkeypatch.setattr(
        converter_service.time,
        "monotonic",
        lambda: clock["now"],
    )

    if checkpoint == "after-all-published":
        original_move = converter_routes._move_into_uploads
        moves = {"count": 0}

        def expire_after_second_move(*args, **kwargs):
            result = original_move(*args, **kwargs)
            moves["count"] += 1
            if moves["count"] == 2:
                clock["now"] = 301.0
            return result

        monkeypatch.setattr(
            converter_routes,
            "_move_into_uploads",
            expire_after_second_move,
        )
    elif checkpoint == "after-all-metadata":
        original_info = converter_routes._file_info_for_response
        infos = {"count": 0}

        def expire_after_second_info(path):
            result = original_info(path)
            infos["count"] += 1
            if infos["count"] == 2:
                clock["now"] = 301.0
            return result

        monkeypatch.setattr(
            converter_routes,
            "_file_info_for_response",
            expire_after_second_info,
        )
    else:
        original_jsonify = converter_routes.jsonify

        def expire_after_response(*args, **kwargs):
            result = original_jsonify(*args, **kwargs)
            payload = args[0] if args else kwargs
            if isinstance(payload, dict) and "files" in payload:
                clock["now"] = 301.0
            return result

        monkeypatch.setattr(
            converter_routes,
            "jsonify",
            expire_after_response,
        )

    response = client.post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == TIMEOUT_PAYLOAD
    assert previous.exists()
    assert upload.exists()
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == [previous]


def test_successful_batch_contract_exposes_all_files_atomically(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    _install_fake_batch_converter(monkeypatch, converter_routes)
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    assert len(payload["files"]) == 2
    assert len(_generated_files(app.config["UPLOAD_FOLDER"])) == 2
    assert {
        item["name"] for item in payload["files"]
    } == {
        path.name for path in _generated_files(app.config["UPLOAD_FOLDER"])
    }


@pytest.mark.parametrize(
    "endpoint",
    ["/api/convert/merge-a4", "/api/convert/to-pdf-merge"],
)
def test_merge_and_alias_create_one_deadline_each(
    app, monkeypatch, endpoint
):
    from app.routes import converter as converter_routes

    original_runtime = converter_routes.converter_job_runtime
    calls = {"count": 0}

    @contextmanager
    def counting_runtime(seconds):
        calls["count"] += 1
        with original_runtime(seconds) as runtime:
            yield runtime

    def fake_merge(uploads, workdir, normalize, norm_page_size):
        output = Path(workdir) / "unido.pdf"
        output.write_bytes(_pdf_bytes())
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "converter_job_runtime",
        counting_runtime,
    )
    monkeypatch.setattr(
        converter_routes,
        "convert_many_uploads_to_single_pdf",
        fake_merge,
    )
    response = app.test_client().post(
        endpoint,
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert calls["count"] == 1


def test_all_outputs_are_validated_before_first_publication(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    validated = []
    original_validate = converter_routes.validate_converter_output
    original_move = converter_routes._move_into_uploads

    def fake_convert(_upload, target, out_dir):
        output = Path(out_dir) / f"{len(validated)}-{id(out_dir)}.{target}"
        _write_structural_output(output, target)
        return str(output)

    def tracking_validate(path, workdir, target, **kwargs):
        result = original_validate(path, workdir, target, **kwargs)
        validated.append(result)
        return result

    def asserting_move(*args, **kwargs):
        assert len(validated) == 2
        return original_move(*args, **kwargs)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    monkeypatch.setattr(
        converter_routes,
        "validate_converter_output",
        tracking_validate,
    )
    monkeypatch.setattr(
        converter_routes,
        "_move_into_uploads",
        asserting_move,
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert len(validated) == 2


def test_second_invalid_output_prevents_entire_batch_publication(
    app, monkeypatch
):
    from app.routes import converter as converter_routes

    calls = {"count": 0}

    def fake_convert(_upload, target, out_dir):
        output = Path(out_dir) / f"resultado.{target}"
        if calls["count"] == 0:
            _write_structural_output(output, target)
        else:
            output.write_bytes(b"corrupted-output")
        calls["count"] += 1
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(2),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "A conversão não gerou um arquivo válido para download."
    }
    assert _generated_files(app.config["UPLOAD_FOLDER"]) == []


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            ConverterToolUnavailableError(r"C:\segredo\binario.exe"),
            (
                "A ferramenta necessária para esta conversão "
                "não está disponível."
            ),
        ),
        (
            ConverterToolExecutionError("stderr sigiloso"),
            "A ferramenta de conversão não concluiu o processamento.",
        ),
    ],
)
def test_external_tool_errors_are_sanitized(
    app,
    monkeypatch,
    error,
    expected_message,
):
    from app.routes import converter as converter_routes

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    response = app.test_client().post(
        "/api/convert/to-pdf",
        data=_uploads(),
        content_type="multipart/form-data",
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": expected_message}
    body = response.get_data(as_text=True)
    assert "segredo" not in body
    assert "stderr" not in body


@pytest.mark.parametrize(
    ("endpoint", "target", "filename", "payload", "extra"),
    [
        ("/api/convert/to-pdf", "pdf", "entrada.pdf", _pdf_bytes(), {}),
        ("/api/convert/to-docx", "docx", "entrada.pdf", _pdf_bytes(), {}),
        ("/api/convert/to-csv", "csv", "entrada.pdf", _pdf_bytes(), {}),
        ("/api/convert/to-xlsx", "xlsx", "entrada.pdf", _pdf_bytes(), {}),
        (
            "/api/convert/to-xlsm",
            "xlsm",
            "entrada.xlsx",
            _xlsx_bytes(),
            {},
        ),
        (
            "/api/convert",
            "pdf",
            "entrada.pdf",
            _pdf_bytes(),
            {"target": "pdf"},
        ),
    ],
)
def test_converter_http_smoke_with_external_tools_mocked(
    app,
    monkeypatch,
    endpoint,
    target,
    filename,
    payload,
    extra,
):
    from app.routes import converter as converter_routes

    expected_target = target

    def fake_convert(_upload, target, out_dir):
        assert target == expected_target
        output = Path(out_dir) / f"resultado.{target}"
        _write_structural_output(output, target)
        return str(output)

    monkeypatch.setattr(
        converter_routes,
        "convert_upload_to_target",
        fake_convert,
    )
    data = _uploads(filename=filename, payload=payload)
    data.update(extra)
    response = app.test_client().post(
        endpoint,
        data=data,
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["count"] == 1
