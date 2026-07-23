import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from app.services import feedback_service
from app.utils import stats as stats_utils


@pytest.fixture
def feedback_app(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASK_ENV", "testing")
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        ADMIN_TOKEN="test-admin-token",
        FEEDBACK_DIR=str(tmp_path / "feedback"),
    )
    return app


@pytest.fixture
def client(feedback_app):
    return feedback_app.test_client()


def _feedback_file(app) -> Path:
    return Path(app.config["FEEDBACK_DIR"]) / "feedback.jsonl"


def _valid_payload(message="Feedback de teste válido."):
    return {"page": "merge", "type": "problema", "message": message}


def _admin_headers():
    return {"X-Admin-Token": "test-admin-token", "Accept": "application/json"}


@pytest.fixture
def isolated_stats(monkeypatch):
    fixed_now = datetime(2026, 7, 23, 15, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(stats_utils, "_utc_now", lambda: fixed_now)
    with stats_utils._lock:
        stats_utils._metric_events.clear()
    yield fixed_now
    with stats_utils._lock:
        stats_utils._metric_events.clear()


def _stats_request_event(timestamp, *, path="/api/merge", status=200):
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat().replace("+00:00", "Z")
    return {
        "timestamp": timestamp,
        "kind": "request",
        "path": path,
        "status": status,
        "message": None,
    }


def _set_stats_events(*events):
    with stats_utils._lock:
        stats_utils._metric_events.clear()
        stats_utils._metric_events.extend(events)


def _record(index: int):
    return {
        "ts": (datetime(2026, 7, 22, 12, tzinfo=timezone.utc) + timedelta(seconds=index))
        .isoformat()
        .replace("+00:00", "Z"),
        "page": f"page-{index}",
        "type": "sugestao",
        "message": f"Mensagem {index}",
        "app_version": "test-version",
        "request_id": f"req-{index}",
    }


def test_submit_feedback_returns_201_and_writes_once(feedback_app, client):
    response = client.post("/api/feedback", json=_valid_payload())

    assert response.status_code == 201
    data = response.get_json()
    assert data["ok"] is True
    assert re.fullmatch(r"[0-9a-f]{8}", data["request_id"])
    assert set(data) == {"ok", "request_id"}
    assert str(_feedback_file(feedback_app)) not in response.get_data(as_text=True)

    lines = _feedback_file(feedback_app).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message"] == _valid_payload()["message"]
    assert record["request_id"] == data["request_id"]


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (_valid_payload("abcd"), "Mensagem muito curta."),
        (_valid_payload("x" * 2001), "Mensagem muito longa."),
        ({"page": "merge", "type": "outro", "message": "Mensagem válida"}, "Tipo de feedback inválido."),
        ({"page": "merge", "type": "problema"}, "Mensagem obrigatória."),
    ],
)
def test_submit_feedback_validation_errors(client, payload, expected_error):
    response = client.post("/api/feedback", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {"error": expected_error}


def test_submit_feedback_write_error_is_not_success(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        feedback_service,
        "_resolve_feedback_file",
        lambda **_kwargs: tmp_path,
    )

    response = client.post("/api/feedback", json=_valid_payload())

    assert response.status_code == 500
    data = response.get_json()
    assert data.get("ok") is not True
    assert "error" in data
    assert str(tmp_path) not in response.get_data(as_text=True)


def test_submit_feedback_stores_html_as_plain_text(feedback_app, client):
    message = '<script>alert("x")</script><strong>texto</strong>'

    response = client.post("/api/feedback", json=_valid_payload(message))

    assert response.status_code == 201
    record = json.loads(
        _feedback_file(feedback_app).read_text(encoding="utf-8").splitlines()[0]
    )
    assert record["message"] == message
    assert "message" not in response.get_json()


def test_submit_feedback_rejects_missing_csrf_when_enabled(feedback_app):
    feedback_app.config["WTF_CSRF_ENABLED"] = True

    response = feedback_app.test_client().post(
        "/api/feedback",
        json=_valid_payload(),
        headers={"Accept": "application/json"},
        base_url="https://localhost",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "CSRF"
    assert not _feedback_file(feedback_app).exists()


def test_submit_feedback_accepts_valid_csrf(feedback_app):
    feedback_app.config["WTF_CSRF_ENABLED"] = True
    client = feedback_app.test_client()
    page = client.get("/", base_url="https://localhost")
    token = re.search(
        r'name="csrf-token" content="([^"]+)"', page.get_data(as_text=True)
    ).group(1)

    response = client.post(
        "/api/feedback",
        json=_valid_payload(),
        headers={
            "Accept": "application/json",
            "Referer": "https://localhost/",
            "X-CSRFToken": token,
        },
        base_url="https://localhost",
    )

    assert response.status_code == 201
    assert response.get_json()["ok"] is True
    assert _feedback_file(feedback_app).exists()


def test_admin_feedback_rejects_missing_token(client):
    response = client.get("/api/admin/feedback")

    assert response.status_code == 401


def test_admin_feedback_rejects_token_in_query_string(client):
    response = client.get("/api/admin/feedback?token=test-admin-token")

    assert response.status_code == 401


def test_admin_feedback_missing_file_returns_empty_list(feedback_app, client):
    feedback_file = _feedback_file(feedback_app)
    assert not feedback_file.exists()

    response = client.get("/api/admin/feedback", headers=_admin_headers())

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "items": [], "count": 0}
    assert not feedback_file.exists()


def test_admin_feedback_returns_newest_first_and_ignores_invalid_lines(
    feedback_app, client
):
    feedback_file = _feedback_file(feedback_app)
    feedback_file.parent.mkdir(parents=True)
    feedback_file.write_text(
        "\n".join(
            [
                json.dumps(_record(1)),
                "linha inválida",
                "",
                "{}",
                "[]",
                json.dumps(_record(2)),
            ]
        ),
        encoding="utf-8",
    )

    response = client.get("/api/admin/feedback?limit=50", headers=_admin_headers())

    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["count"] == 2
    assert [item["request_id"] for item in data["items"]] == ["req-2", "req-1"]
    assert set(data["items"][0]) == {
        "timestamp",
        "page",
        "type",
        "message",
        "app_version",
        "request_id",
    }
    assert str(feedback_file) not in response.get_data(as_text=True)


def test_admin_feedback_caps_limit_at_100(feedback_app, client):
    feedback_file = _feedback_file(feedback_app)
    feedback_file.parent.mkdir(parents=True)
    feedback_file.write_text(
        "\n".join(json.dumps(_record(index)) for index in range(120)),
        encoding="utf-8",
    )

    response = client.get("/api/admin/feedback?limit=999", headers=_admin_headers())

    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 100
    assert data["items"][0]["request_id"] == "req-119"
    assert data["items"][-1]["request_id"] == "req-20"
    assert "FEEDBACK_DIR" not in response.get_data(as_text=True)
    assert str(feedback_file.parent) not in response.get_data(as_text=True)


def test_admin_feedback_clamps_limit_to_one(feedback_app, client):
    feedback_file = _feedback_file(feedback_app)
    feedback_file.parent.mkdir(parents=True)
    feedback_file.write_text(
        "\n".join(json.dumps(_record(index)) for index in range(3)),
        encoding="utf-8",
    )

    response = client.get("/api/admin/feedback?limit=0", headers=_admin_headers())

    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1
    assert data["items"][0]["request_id"] == "req-2"


def test_admin_page_starts_locked_and_preserves_dashboard_hooks(client):
    response = client.get("/admin", base_url="https://localhost")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Dashboard administrativo" in html
    assert "Informe o token administrativo para carregar o dashboard." in html
    assert re.search(r'id="admin-dashboard"[^>]*\shidden(?:\s|>)', html)
    for element_id in {
        "adm-token",
        "btn-reload",
        "range",
        "autorf",
        "summary-cards",
        "tools-grid",
        "feedback-container",
        "logs-container",
    }:
        assert html.count(f'id="{element_id}"') == 1
    assert "style=" not in html


def test_admin_stats_missing_range_defaults_to_15m(client, isolated_stats):
    response = client.get("/api/admin/stats", headers=_admin_headers())

    assert response.status_code == 200
    data = response.get_json()
    assert data["range"] == "15m"
    assert len(data["timeseries"]["requests_per_min"]) == 15


@pytest.mark.parametrize(
    ("range_spec", "age", "expected_points"),
    [
        ("15m", timedelta(minutes=5), 15),
        ("1h", timedelta(minutes=30), 60),
        ("24h", timedelta(hours=12), 1440),
    ],
)
def test_admin_stats_accepts_supported_ranges(
    client, isolated_stats, range_spec, age, expected_points
):
    _set_stats_events(_stats_request_event(isolated_stats - age))

    response = client.get(
        f"/api/admin/stats?range={range_spec}",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["range"] == range_spec
    assert data["requests_total"] == 1
    assert len(data["timeseries"]["requests_per_min"]) == expected_points


def test_admin_stats_rejects_unknown_range(client, isolated_stats):
    response = client.get(
        "/api/admin/stats?range=7d",
        headers=_admin_headers(),
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Período inválido. Use 15m, 1h ou 24h.",
    }


def test_admin_stats_filters_all_metrics_by_range(client, isolated_stats):
    _set_stats_events(
        _stats_request_event(
            isolated_stats - timedelta(minutes=16),
            path="/api/compress",
            status=500,
        ),
        _stats_request_event(
            isolated_stats - timedelta(minutes=5),
            path="/api/merge",
            status=200,
        ),
        _stats_request_event(
            isolated_stats - timedelta(minutes=4),
            path="/api/merge",
            status=500,
        ),
        _stats_request_event(
            isolated_stats - timedelta(minutes=3),
            path="/api/split",
            status=404,
        ),
        _stats_request_event(
            "timestamp-invalido",
            path="/api/compress",
            status=500,
        ),
    )

    response = client.get(
        "/api/admin/stats?range=15m",
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["requests_total"] == 3
    assert data["status"] == {"2xx": 1, "4xx": 1, "5xx": 1}
    assert data["tools"] == {
        "merge_ok": 1,
        "merge_err": 1,
        "split_err": 1,
    }
    assert sum(
        point["count"]
        for point in data["timeseries"]["requests_per_min"]
    ) == 3
    assert {item["status"] for item in data["recent_errors"]} == {404, 500}
    assert all(item["path"] != "/api/compress" for item in data["recent_errors"])
    assert "folder" not in data["uploads"]


def test_admin_stats_requires_token(client, isolated_stats):
    response = client.get("/api/admin/stats?range=15m")

    assert response.status_code == 401


def test_admin_template_versions_admin_script_with_app_version(
    feedback_app, client
):
    feedback_app.config["APP_VERSION"] = "admin-cache-test"

    response = client.get("/admin", base_url="https://localhost")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count("js/admin.js") == 1
    assert "/static/js/admin.js?v=admin-cache-test" in html


def test_footer_is_canonical_and_uses_dynamic_year_and_config_version(
    feedback_app, client
):
    feedback_app.config["APP_VERSION"] = "version-from-app-config"

    response = client.get("/", base_url="https://localhost")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    current_year = datetime.now(timezone.utc).year
    assert html.count('<footer id="site-footer"') == 1
    assert f"© {current_year} Grupo Vital" in html
    assert "v version-from-app-config" in html
    assert "data-feedback-open" in html
    assert "Sugestões? Entre em contato" not in html
    assert "luisb@grupovital.com.br" not in html
