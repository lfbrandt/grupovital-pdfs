from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

import pytest
from PyPDF2 import PdfReader, PdfWriter

from app import create_app
from app.services import compress_service
from tests.pdf_fixture_factory import (
    inspect_pdf,
    make_plain_pdf,
    make_synthetic_pdf,
)


@pytest.fixture
def app(tmp_path):
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=tmp_path,
    )
    return application


def _pdf_with_pages(path: Path, page_count: int) -> Path:
    writer = PdfWriter()
    for index in range(page_count):
        writer.add_blank_page(width=595 + index, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _patch_thumbnails(monkeypatch):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "_generate_page_thumbnail",
        lambda _path, page_index: (
            f"data:image/svg+xml;base64,page-{page_index + 1}"
        ),
    )
    return compress_routes


def _analyze(client, source: Path, monkeypatch) -> str:
    _patch_thumbnails(monkeypatch)
    response = client.post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(source.read_bytes()), "target.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["analyse_id"]


def _target_settings(page_count: int = 2) -> list[dict]:
    return [
        {
            "page_number": page_number,
            "include": True,
            "keep_original": False,
            "resize_to_a4": False,
            "quality": 80,
            "dpi": 100,
        }
        for page_number in range(1, page_count + 1)
    ]


def _process_target(
    client,
    analyse_id: str,
    *,
    target_size_mb: float = 5,
    settings: list[dict] | None = None,
    rotations=None,
    allow_grayscale: bool = False,
):
    return client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "mode": "target_size",
            "target_size_mb": target_size_mb,
            "allow_grayscale": allow_grayscale,
            "page_settings": settings or _target_settings(),
            "rotations": rotations,
        },
        headers={"Accept": "application/pdf"},
    )


class TargetAttemptHarness:
    def __init__(
        self,
        monkeypatch,
        *,
        baseline_size: int,
        outcomes: list[dict],
    ):
        from app.routes import compress as compress_routes

        self.routes = compress_routes
        self.baseline_size = baseline_size
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.candidate_paths: list[str] = []
        self.sizes: dict[str, int] = {}
        self._real_getsize = os.path.getsize
        self._real_build_baseline = compress_routes.build_selected_baseline

        monkeypatch.setattr(
            compress_routes,
            "build_selected_baseline",
            self._build_baseline,
        )
        monkeypatch.setattr(
            compress_routes,
            "_run_target_profile_attempt",
            self._run_attempt,
        )
        monkeypatch.setattr(
            compress_routes.os.path,
            "getsize",
            self._getsize,
        )

    @staticmethod
    def _key(path) -> str:
        return os.path.normcase(os.path.realpath(os.fspath(path)))

    def _getsize(self, path) -> int:
        key = self._key(path)
        if key in self.sizes:
            return self.sizes[key]
        return self._real_getsize(path)

    def _build_baseline(self, *args, **kwargs):
        self._real_build_baseline(*args, **kwargs)
        output_path = args[1] if len(args) > 1 else kwargs["output_path"]
        self.sizes[self._key(output_path)] = self.baseline_size

    def _run_attempt(self, **kwargs):
        if len(self.calls) >= len(self.outcomes):
            raise AssertionError("algoritmo excedeu as tentativas previstas")

        outcome = self.outcomes[len(self.calls)]
        profile = dict(kwargs["profile"])
        candidate_path = kwargs["candidate_path"]
        baseline_path = kwargs["baseline_path"]
        self.calls.append(
            {
                "baseline_path": baseline_path,
                "candidate_path": candidate_path,
                "profile": profile,
                "compress_positions": list(kwargs["compress_positions"]),
                "keep_positions": list(kwargs["keep_positions"]),
                "timeout_seconds": kwargs["timeout_seconds"],
            }
        )
        self.candidate_paths.append(candidate_path)

        kind = outcome.get("kind", "valid")
        if kind == "raise":
            raise RuntimeError("controlled target attempt failure")
        if kind == "unreadable":
            Path(candidate_path).write_bytes(b"not-a-pdf")
        elif kind == "page_loss":
            _pdf_with_pages(Path(candidate_path), 1)
        else:
            shutil.copyfile(baseline_path, candidate_path)

        self.sizes[self._key(candidate_path)] = outcome["size"]
        return compress_service.CompressionGroupWarnings()


def _assert_target_headers(
    response,
    *,
    achieved: bool,
    attempts: int,
    profile: str,
):
    assert response.headers["X-Target-Size-Bytes"] == "4950000"
    assert response.headers["X-Target-Achieved"] == (
        "true" if achieved else "false"
    )
    assert response.headers["X-Compression-Attempts"] == str(attempts)
    assert response.headers["X-Compression-Profile"] == profile
    assert "X-Size-Original-KB" in response.headers
    assert "X-Size-Uploaded-Bytes" in response.headers
    assert "X-Size-Baseline-Bytes" in response.headers
    assert "X-Size-Final-Bytes" in response.headers
    assert "X-Size-Baseline-KB" in response.headers
    assert "X-Size-Final-KB" in response.headers
    assert "X-Reduction-Pct" in response.headers
    assert "X-Baseline-Reduction-Pct" in response.headers
    assert "X-Fallback" in response.headers
    assert "X-Compress-Warnings" in response.headers
    assert "X-Compression-Elapsed-Sec" in response.headers
    exposed = response.headers["Access-Control-Expose-Headers"]
    for header in (
        "X-Size-Uploaded-Bytes",
        "X-Size-Baseline-Bytes",
        "X-Size-Final-Bytes",
        "X-Target-Size-Bytes",
        "X-Target-Achieved",
        "X-Compression-Attempts",
        "X-Compression-Profile",
        "X-Compression-Elapsed-Sec",
        "X-Compress-Warnings",
    ):
        assert header in exposed


def test_target_mode_baseline_already_below_limit_skips_ghostscript(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=4_000_000,
        outcomes=[],
    )
    source = make_plain_pdf(tmp_path / "already-small.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert harness.calls == []
    _assert_target_headers(
        response,
        achieved=True,
        attempts=0,
        profile="baseline",
    )
    assert response.headers["X-Fallback"] == "selected_baseline"
    response.close()


def test_target_mode_refines_first_success_with_more_conservative_profile(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[
            {"size": 4_800_000},
            {"size": 4_900_000},
        ],
    )
    source = make_plain_pdf(tmp_path / "first-attempt.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert [call["profile"]["slug"] for call in harness.calls] == [
        "medio",
        "equilibrado",
    ]
    _assert_target_headers(
        response,
        achieved=True,
        attempts=2,
        profile="equilibrado",
    )
    assert response.headers["X-Fallback"] == "final_compressed"
    assert response.headers["X-Size-Uploaded-Bytes"] == str(source.stat().st_size)
    assert response.headers["X-Size-Baseline-Bytes"] == "10000000"
    assert response.headers["X-Size-Final-Bytes"] == "4900000"
    assert response.headers["X-Size-Original-KB"] == str(
        round(source.stat().st_size / 1024, 1)
    )
    assert response.headers["X-Baseline-Reduction-Pct"] == "51.0"
    response.close()


def test_target_mode_first_profile_has_no_more_conservative_retry(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=5_700_000,
        outcomes=[{"size": 4_800_000}],
    )
    source = make_plain_pdf(tmp_path / "first-profile.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert [call["profile"]["slug"] for call in harness.calls] == [
        "conservador"
    ]
    _assert_target_headers(
        response,
        achieved=True,
        attempts=1,
        profile="conservador",
    )
    response.close()


def test_target_mode_uses_second_attempt_when_needed(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=20_000_000,
        outcomes=[
            {"size": 6_000_000},
            {"size": 4_800_000},
        ],
    )
    source = make_plain_pdf(tmp_path / "second-attempt.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert [call["profile"]["slug"] for call in harness.calls] == [
        "agressivo",
        "maximo_seguro",
    ]
    _assert_target_headers(
        response,
        achieved=True,
        attempts=2,
        profile="maximo_seguro",
    )
    response.close()


def test_target_mode_selects_highest_quality_candidate_below_target(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[
            {"size": 6_000_000},
            {"size": 4_500_000},
            {"size": 4_800_000},
        ],
    )
    source = make_plain_pdf(tmp_path / "best-quality.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert [call["profile"]["slug"] for call in harness.calls] == [
        "medio",
        "maximo_seguro",
        "forte",
    ]
    _assert_target_headers(
        response,
        achieved=True,
        attempts=3,
        profile="forte",
    )
    assert response.headers["X-Size-Final-KB"] == str(
        round(4_800_000 / 1024, 1)
    )
    response.close()


def test_target_mode_not_achieved_uses_smallest_safe_candidate_and_jpeg_retry(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[
            {"size": 8_000_000},
            {"size": 6_000_000},
            {"size": 5_310_000},
        ],
    )
    source = make_plain_pdf(tmp_path / "not-achieved.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert len(harness.calls) == compress_service.MAX_TARGET_COMPRESSION_ATTEMPTS
    assert {
        call["baseline_path"] for call in harness.calls
    } == {harness.calls[0]["baseline_path"]}
    assert all(
        call["baseline_path"] not in harness.candidate_paths
        for call in harness.calls
    )
    assert harness.calls[-1]["profile"] == (
        compress_service.TARGET_JPEG_RECOMPRESSION_PROFILE
    )
    _assert_target_headers(
        response,
        achieved=False,
        attempts=3,
        profile="recompressao_jpeg_agressiva",
    )
    assert response.headers["X-Fallback"] == "target_not_achieved"
    assert "recompressao_jpeg_agressiva" in (
        response.headers["X-Compress-Warnings"]
    )
    assert all(
        call["timeout_seconds"]
        <= app.config.get(
            "TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC",
            90,
        ) // compress_service.MAX_TARGET_COMPRESSION_ATTEMPTS
        < compress_service.GHOSTSCRIPT_TIMEOUT
        for call in harness.calls
    )
    response.close()


def test_target_mode_total_timeout_returns_best_safe_candidate(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    app.config["TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC"] = 90
    clock_values = iter([100.0, 100.0, 191.0, 191.0])
    monkeypatch.setattr(
        compress_routes,
        "_target_clock",
        lambda: next(clock_values),
    )
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[{"size": 6_000_000}],
    )
    source = make_plain_pdf(tmp_path / "budget.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert len(harness.calls) == 1
    assert harness.calls[0]["timeout_seconds"] == 30
    assert response.headers["X-Size-Final-Bytes"] == "6000000"
    assert response.headers["X-Compression-Attempts"] == "1"
    assert response.headers["X-Compression-Elapsed-Sec"] == "91.0"
    assert "target_timeout_budget_exhausted" in (
        response.headers["X-Compress-Warnings"]
    )
    response.close()


def test_target_mode_grayscale_opt_in_runs_only_as_last_attempt(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[
            {"size": 8_000_000},
            {"size": 6_000_000},
            {"size": 4_800_000},
        ],
    )
    source = make_plain_pdf(tmp_path / "grayscale.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(
        client,
        analyse_id,
        allow_grayscale=True,
    )

    assert response.status_code == 200
    assert [call["profile"]["slug"] for call in harness.calls] == [
        "medio",
        "maximo_seguro",
        "tons_de_cinza",
    ]
    assert harness.calls[-1]["profile"] == compress_service.TARGET_GRAYSCALE_PROFILE
    _assert_target_headers(
        response,
        achieved=True,
        attempts=3,
        profile="tons_de_cinza",
    )
    assert "tons_de_cinza_aplicados" in (
        response.headers["X-Compress-Warnings"]
    )
    response.close()


@pytest.mark.parametrize("invalid_kind", ["page_loss", "unreadable"])
def test_target_mode_rejects_invalid_candidate_and_cleans_it(
    app, tmp_path, monkeypatch, invalid_kind
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[
            {"size": 4_000_000, "kind": invalid_kind},
            {"size": 4_700_000},
            {"size": 6_000_000},
        ],
    )
    source = make_plain_pdf(tmp_path / f"invalid-{invalid_kind}.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id)

    assert response.status_code == 200
    assert not Path(harness.candidate_paths[0]).exists()
    assert Path(harness.candidate_paths[1]).exists()
    assert not Path(harness.candidate_paths[2]).exists()
    _assert_target_headers(
        response,
        achieved=True,
        attempts=3,
        profile="maximo_seguro",
    )
    response.close()


def test_target_mode_failure_cleans_candidate_and_allows_retry(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[
            {"size": 0, "kind": "raise"},
            {"size": 4_800_000},
            {"size": 5_200_000},
        ],
    )
    source = make_plain_pdf(tmp_path / "retry.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)
    session_file = tmp_path / f".session_{analyse_id}"

    failed = _process_target(client, analyse_id)

    assert failed.status_code == 500
    assert session_file.exists()
    assert not Path(harness.candidate_paths[0]).exists()
    assert not (tmp_path / f".compress_lock_{analyse_id}").exists()

    retry = _process_target(client, analyse_id)
    assert retry.status_code == 200
    _assert_target_headers(
        retry,
        achieved=True,
        attempts=2,
        profile="medio",
    )
    retry.close()
    assert not session_file.exists()


def test_target_mode_active_lock_still_returns_409(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    source = make_plain_pdf(tmp_path / "locked.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)
    token = compress_routes._acquire_process_lock(analyse_id, str(tmp_path))
    assert token
    try:
        response = _process_target(client, analyse_id)
        assert response.status_code == 409
    finally:
        compress_routes._release_process_lock(
            analyse_id,
            str(tmp_path),
            token,
        )


def test_target_mode_interactive_document_preserves_content_without_attempt(
    app, tmp_path, monkeypatch
):
    harness = TargetAttemptHarness(
        monkeypatch,
        baseline_size=10_000_000,
        outcomes=[],
    )
    source = make_synthetic_pdf(tmp_path / "interactive.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)

    response = _process_target(client, analyse_id, allow_grayscale=True)

    assert response.status_code == 200
    assert harness.calls == []
    _assert_target_headers(
        response,
        achieved=False,
        attempts=0,
        profile="baseline",
    )
    assert "interactive_content_preserved" in (
        response.headers["X-Compress-Warnings"]
    )
    assert "tons_de_cinza_aplicados" not in (
        response.headers["X-Compress-Warnings"]
    )
    output = tmp_path / "interactive-result.pdf"
    output.write_bytes(response.data)
    info = inspect_pdf(output)
    assert info["has_acroform"] is True
    assert info["signature_widgets"]
    response.close()


@pytest.mark.parametrize(
    ("mode", "target"),
    [
        ("unknown", 5),
        ("target_size", None),
        ("target_size", 0),
        ("target_size", -1),
        ("target_size", 0.19),
        ("target_size", "5"),
        ("target_size", True),
        ("target_size", 50.01),
    ],
)
def test_target_mode_rejects_invalid_mode_or_target(app, mode, target):
    payload = {
        "analyse_id": "a" * 32,
        "mode": mode,
        "page_settings": _target_settings(1),
    }
    if target is not None:
        payload["target_size_mb"] = target

    response = app.test_client().post(
        "/api/compress/process-with-settings",
        json=payload,
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert set(response.get_json()) == {"error"}


def test_target_mode_accepts_minimum_target_and_applies_margin():
    from app.routes import compress as compress_routes

    target_mb, target_bytes = compress_routes._normalize_target_size(0.20)

    assert target_mb == compress_routes.Decimal("0.20")
    assert target_bytes == 198_000


def test_target_mode_rejects_non_boolean_grayscale_option(app):
    response = app.test_client().post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": "a" * 32,
            "mode": "target_size",
            "target_size_mb": 5,
            "allow_grayscale": "true",
            "page_settings": _target_settings(1),
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert set(response.get_json()) == {"error"}


def test_target_mode_has_stricter_rate_limit_than_manual(tmp_path):
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=True,
        UPLOAD_FOLDER=tmp_path,
    )
    client = application.test_client()
    target_payload = {
        "analyse_id": "a" * 32,
        "mode": "target_size",
        "target_size_mb": 5,
        "page_settings": _target_settings(1),
    }
    manual_payload = {
        "analyse_id": "b" * 32,
        "mode": "manual",
        "page_settings": _target_settings(1),
    }

    target_responses = [
        client.post(
            "/api/compress/process-with-settings",
            json=target_payload,
            environ_base={"REMOTE_ADDR": "198.51.100.41"},
        )
        for _ in range(3)
    ]
    manual_responses = [
        client.post(
            "/api/compress/process-with-settings",
            json=manual_payload,
            environ_base={"REMOTE_ADDR": "198.51.100.42"},
        )
        for _ in range(6)
    ]

    assert [response.status_code for response in target_responses] == [
        404,
        404,
        429,
    ]
    assert [response.status_code for response in manual_responses] == [
        404,
        404,
        404,
        404,
        404,
        429,
    ]


def test_manual_mode_remains_compatible(app, tmp_path, monkeypatch):
    from app.routes import compress as compress_routes

    calls = []

    def fake_group(**kwargs):
        calls.append(kwargs)
        compress_service._apply_rotations_pikepdf(
            kwargs["input_path"],
            kwargs["pages"],
            kwargs["rotations"],
            kwargs["output_path"],
        )
        return compress_service.CompressionGroupWarnings(
            fallback_reason="gs_larger"
        )

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        fake_group,
    )
    source = make_plain_pdf(tmp_path / "manual.pdf")
    client = app.test_client()
    analyse_id = _analyze(client, source, monkeypatch)
    response = client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "mode": "manual",
            "page_settings": [
                {
                    "page_number": 1,
                    "include": True,
                    "quality": 95,
                    "dpi": 200,
                    "resize_to_a4": False,
                },
                {"page_number": 2, "include": False},
            ],
        },
        headers={"Accept": "application/pdf"},
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["quality"] == 95
    assert calls[0]["dpi"] == 200
    assert calls[0]["resize_to_a4"] is False
    assert "X-Target-Size-Bytes" not in response.headers
    response.close()


def test_target_route_requires_csrf_when_enabled(tmp_path):
    csrf_app = create_app()
    csrf_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=tmp_path,
    )
    response = csrf_app.test_client().post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": "a" * 32,
            "mode": "target_size",
            "target_size_mb": 5,
            "page_settings": _target_settings(1),
        },
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "CSRF"


def test_target_jpeg_recompression_flags_are_opt_in():
    params = compress_service._build_gs_image_params(25, 72)
    regular = compress_service._build_gs_args(
        "input.pdf",
        "regular.pdf",
        params,
    )
    aggressive = compress_service._build_gs_args(
        "input.pdf",
        "aggressive.pdf",
        params,
        force_jpeg_recompression=True,
    )
    grayscale = compress_service._build_gs_args(
        "input.pdf",
        "grayscale.pdf",
        params,
        force_jpeg_recompression=True,
        convert_to_grayscale=True,
    )

    for flag in (
        "-dPassThroughJPEGImages=false",
        "-dPassThroughJPXImages=false",
    ):
        assert flag not in regular
        assert flag in aggressive
        assert flag in grayscale
    assert "-sColorConversionStrategy=Gray" not in regular
    assert "-sColorConversionStrategy=Gray" not in aggressive
    assert "-sColorConversionStrategy=Gray" in grayscale

    formatted = compress_service._fmt_gs_cmd(
        [
            "gs",
            (
                "-sOutputFile=C:\\private\\target_candidate_"
                "0123456789abcdef.pdf"
            ),
        ]
    )
    assert "private" not in formatted
    assert "0123456789abcdef" not in formatted


def test_target_jpeg_recompression_reaches_shared_executor(
    app, tmp_path, monkeypatch
):
    source = _pdf_with_pages(tmp_path / "jpeg-source.pdf", 1)
    output = tmp_path / "jpeg-output.pdf"
    captured = {}

    def fake_executor(
        input_pdf,
        output_pdf,
        quality,
        dpi,
        *,
        expected_pages=None,
        force_jpeg_recompression=False,
    ):
        captured.update(
            {
                "input_pdf": input_pdf,
                "quality": quality,
                "dpi": dpi,
                "expected_pages": expected_pages,
                "force_jpeg_recompression": force_jpeg_recompression,
            }
        )
        shutil.copyfile(input_pdf, output_pdf)
        size = os.path.getsize(output_pdf)
        return compress_service.GhostscriptExecution(
            usable=True,
            fallback_reason=None,
            input_size=size + 1000,
            output_size=size,
            expected_pages=expected_pages,
            actual_pages=expected_pages,
            returncode=0,
        )

    monkeypatch.setattr(
        compress_service,
        "execute_ghostscript_validated",
        fake_executor,
    )
    with app.app_context():
        warnings = compress_service.comprimir_pdf_com_params(
            str(source),
            str(output),
            pages=[1],
            quality=25,
            dpi=72,
            force_jpeg_recompression=True,
        )

    assert warnings.used_original is False
    assert captured["quality"] == 25
    assert captured["dpi"] == 72
    assert captured["expected_pages"] == 1
    assert captured["force_jpeg_recompression"] is True


def test_target_grayscale_reaches_shared_executor_with_attempt_timeout(
    app, tmp_path, monkeypatch
):
    source = _pdf_with_pages(tmp_path / "gray-source.pdf", 1)
    output = tmp_path / "gray-output.pdf"
    captured = {}

    def fake_executor(
        input_pdf,
        output_pdf,
        quality,
        dpi,
        *,
        expected_pages=None,
        force_jpeg_recompression=False,
        convert_to_grayscale=False,
        timeout_seconds=None,
    ):
        captured.update(
            {
                "quality": quality,
                "dpi": dpi,
                "expected_pages": expected_pages,
                "force_jpeg_recompression": force_jpeg_recompression,
                "convert_to_grayscale": convert_to_grayscale,
                "timeout_seconds": timeout_seconds,
            }
        )
        shutil.copyfile(input_pdf, output_pdf)
        size = os.path.getsize(output_pdf)
        return compress_service.GhostscriptExecution(
            usable=True,
            fallback_reason=None,
            input_size=size + 1000,
            output_size=size,
            expected_pages=expected_pages,
            actual_pages=expected_pages,
            returncode=0,
        )

    monkeypatch.setattr(
        compress_service,
        "execute_ghostscript_validated",
        fake_executor,
    )
    with app.app_context():
        warnings = compress_service.comprimir_pdf_com_params(
            str(source),
            str(output),
            pages=[1],
            quality=25,
            dpi=72,
            force_jpeg_recompression=True,
            convert_to_grayscale=True,
            timeout_seconds=7,
        )

    assert warnings.used_original is False
    assert captured == {
        "quality": 25,
        "dpi": 72,
        "expected_pages": 1,
        "force_jpeg_recompression": True,
        "convert_to_grayscale": True,
        "timeout_seconds": 7,
    }


def test_target_mode_template_and_frontend_contract():
    template = Path("app/templates/compress.html").read_text(encoding="utf-8")
    source = Path("app/static/js/compress.js").read_text(encoding="utf-8")

    assert 'value="target_size" checked' in template
    assert 'value="manual"' in template
    assert 'id="target-size-mb"' in template
    assert 'min="0.20"' in template
    assert 'data-target-size-mb="1"' in template
    assert 'data-target-size-mb="5"' in template
    assert 'data-target-size-mb="10"' in template
    assert (
        "Buscaremos a melhor qualidade possível abaixo desse limite"
        in template
    )
    assert "payload.target_size_mb = _AState.targetSizeMb" in source
    assert "payload.allow_grayscale = _AState.allowGrayscale" in source
    assert 'id="allow-grayscale"' in template
    assert "Permitir tons de cinza para tentar atingir a meta" in template
    assert "Arquivo enviado" in template
    assert "Baseline selecionado" in template
    assert (
        "Recompressão agressiva aplicada. Confira textos pequenos e imagens antes de enviar."
        in source
    )
    assert "'X-CSRFToken':  readCSRFToken()" in source


def test_target_attempt_with_keep_original_rebuilds_from_same_baseline(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    baseline = _pdf_with_pages(tmp_path / "baseline.pdf", 3)
    candidate = tmp_path / "candidate.pdf"
    temporary_files = []
    calls = []

    def fake_group(**kwargs):
        calls.append(kwargs)
        compress_service._apply_rotations_pikepdf(
            kwargs["input_path"],
            kwargs["pages"],
            None,
            kwargs["output_path"],
        )
        return compress_service.CompressionGroupWarnings()

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        fake_group,
    )
    with app.app_context():
        compress_routes._run_target_profile_attempt(
            baseline_path=str(baseline),
            candidate_path=str(candidate),
            profile=compress_service.TARGET_SIZE_PROFILES[3],
            compress_positions=[1, 3],
            keep_positions=[2],
            upload_folder=str(tmp_path),
            temporary_files=temporary_files,
            timeout_seconds=30,
        )

    assert calls[0]["input_path"] == str(baseline)
    assert calls[0]["pages"] == [1, 3]
    assert calls[0]["timeout_seconds"] == 30
    reader = PdfReader(str(candidate))
    assert len(reader.pages) == 3
    assert [float(page.mediabox.width) for page in reader.pages] == [
        595.0,
        596.0,
        597.0,
    ]
    assert all(not Path(path).exists() for path in temporary_files)
