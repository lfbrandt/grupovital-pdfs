import os
from app import create_app


def test_load_env_based_on_flask_env(monkeypatch, tmp_path):
    envdir = tmp_path / "envs"
    envdir.mkdir()
    env_file = envdir / ".env.custom"
    env_file.write_text("MY_TEST_VAR=42\n")
    monkeypatch.setenv("FLASK_ENV", "custom")
    monkeypatch.chdir(tmp_path)
    create_app()
    assert os.environ.get("MY_TEST_VAR") == "42"
