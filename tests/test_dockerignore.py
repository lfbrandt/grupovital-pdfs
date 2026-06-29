from fnmatch import fnmatchcase
from pathlib import Path


DOCKERIGNORE = Path(__file__).resolve().parents[1] / ".dockerignore"


def _rules():
    return [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _clean_rule(rule):
    negated = rule.startswith("!")
    if negated:
        rule = rule[1:]
    return negated, rule.replace("\\", "/").lstrip("/")


def _directory_rule_matches(pattern, path, is_dir):
    pattern = pattern.rstrip("/")
    if "/" in pattern:
        return path == pattern or path.startswith(f"{pattern}/")

    parts = path.split("/")
    for index, part in enumerate(parts):
        if fnmatchcase(part, pattern) and (is_dir or index < len(parts) - 1):
            return True
    return False


def _rule_matches(rule, path, is_dir=False):
    _, pattern = _clean_rule(rule)
    path = path.replace("\\", "/").strip("/")
    if not pattern:
        return False
    if pattern.endswith("/"):
        return _directory_rule_matches(pattern, path, is_dir)
    if "/" not in pattern:
        return any(fnmatchcase(part, pattern) for part in path.split("/"))
    return fnmatchcase(path, pattern)


def _included_by_docker_context(rules, path, is_dir=False):
    included = True
    for rule in rules:
        negated, _ = _clean_rule(rule)
        if _rule_matches(rule, path, is_dir=is_dir):
            included = negated
    return included


def _assert_excluded(rules, path, reason, is_dir=False):
    assert not _included_by_docker_context(
        rules, path, is_dir=is_dir
    ), f"{path} should be excluded from the Docker context: {reason}"


def _assert_included(rules, path, reason, is_dir=False):
    assert _included_by_docker_context(
        rules, path, is_dir=is_dir
    ), f"{path} should remain in the Docker context: {reason}"


def _rule_index(rules, expected):
    normalized = [_clean_rule(rule)[1] for rule in rules]
    expected = expected.lstrip("!").replace("\\", "/").lstrip("/")
    return normalized.index(expected)


def test_env_files_are_excluded_without_reading_real_env_files():
    rules = _rules()

    assert _rule_index(rules, ".env") >= 0
    assert _rule_index(rules, ".env.*") >= 0
    assert _rule_index(rules, "envs/.env*") >= 0
    assert _rule_index(rules, "!.env.example") > _rule_index(rules, ".env.*")
    assert _rule_index(rules, "!envs/.env.example") > _rule_index(
        rules, "envs/.env*"
    )
    assert _rule_matches("envs/.env*", "envs/.env.production")

    for path in (
        ".env",
        ".env.production",
        ".env.development",
        ".env.testing",
        "envs/.env.production",
        "envs/.env.development",
        "envs/.env.testing",
    ):
        _assert_excluded(rules, path, "real environment files must not be copied")

    _assert_included(rules, ".env.example", "root example env file is safe docs")
    _assert_included(rules, "envs/.env.example", "env example file is safe docs")


def test_local_runtime_and_dependency_artifacts_are_excluded():
    rules = _rules()

    blocked_paths = {
        ".venv/bin/python": "virtual environment",
        "venv/Scripts/python.exe": "virtual environment",
        "venv_old/pyvenv.cfg": "old virtual environment",
        "venv-test_old/pyvenv.cfg": "old test virtual environment",
        "node_modules/package/index.js": "local JavaScript dependencies",
        "uploads/document.pdf": "runtime uploads",
        "app/uploads/document.pdf": "runtime uploads",
        "logs/app.log": "runtime logs",
        "app.log": "root application log",
        "app.log.1": "rotated application log",
        "tmp/work.tmp": "temporary files",
        "temp/work.temp": "temporary files",
    }

    for path, reason in blocked_paths.items():
        _assert_excluded(rules, path, reason)


def test_python_caches_editor_files_and_git_metadata_are_excluded():
    rules = _rules()

    for path, reason in {
        "__pycache__/module.cpython-310.pyc": "Python cache directory",
        "app/__pycache__/module.cpython-310.pyc": "nested Python cache directory",
        "module.pyc": "Python bytecode",
        ".pytest_cache/v/cache/nodeids": "pytest cache",
        ".mypy_cache/3.10/app.meta.json": "mypy cache",
        ".ruff_cache/content": "ruff cache",
        ".coverage": "coverage data",
        "htmlcov/index.html": "coverage report",
        ".vscode/settings.json": "editor settings",
        ".idea/workspace.xml": "editor settings",
        ".DS_Store": "macOS metadata",
        "Thumbs.db": "Windows metadata",
        ".git/config": "Git internals",
        ".gitignore": "Git ignore metadata",
    }.items():
        _assert_excluded(rules, path, reason)


def test_required_build_inputs_remain_available():
    rules = _rules()

    for path, is_dir in (
        ("app/routes/merge.py", False),
        ("requirements.txt", False),
        ("package.json", False),
        ("package-lock.json", False),
        ("run.py", False),
        ("templates", True),
        ("static", True),
        ("envs/.env.example", False),
    ):
        _assert_included(rules, path, "required build input", is_dir=is_dir)
