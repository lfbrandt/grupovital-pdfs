"""
Tests for _get_gs_cmd() binary resolution in compress_service.

Resolution order (new canonical behaviour):
  1. GS_BIN          env var
  2. GHOSTSCRIPT_BIN env var
  3. GS_PATH         env var  (legacy alias)
  4. auto-detect:    gswin64c → gswin32c → gs  (via shutil.which)
  5. blind fallback: 'gs'

Each test resets the module-level cache (_GS_CMD_CACHE) before running so
that monkeypatched env vars / shutil.which are re-evaluated cleanly.
"""
import shutil
from io import BytesIO

import pytest
from PyPDF2 import PdfWriter
from werkzeug.datastructures import FileStorage

from app import create_app
from app.services import compress_service


# ── helpers ───────────────────────────────────────────────────────────────────

def _simple_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def _reset_gs_cache(monkeypatch):
    """Force _get_gs_cmd() to re-resolve on next call."""
    monkeypatch.setattr(compress_service, '_GS_CMD_CACHE', None)


def _fake_run_factory(called: dict):
    """
    subprocess.run stub that records cmd[0] and creates the output file
    so Ghostscript's output-existence check doesn't raise.
    """
    def fake_run(cmd, **kwargs):
        called['bin'] = cmd[0]
        for part in cmd:
            s = str(part)
            if s.startswith('-sOutputFile='):
                open(s.split('=', 1)[1], 'wb').close()
        # Return a minimal CompletedProcess-like object
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
    return fake_run


# ── env-var priority tests ────────────────────────────────────────────────────

def test_gs_bin_takes_priority_over_ghostscript_bin(monkeypatch, tmp_path):
    """GS_BIN must win even when GHOSTSCRIPT_BIN and GS_PATH are also set."""
    _reset_gs_cache(monkeypatch)
    monkeypatch.setenv('GS_BIN',          '/fake/gs_bin')
    monkeypatch.setenv('GHOSTSCRIPT_BIN', '/fake/ghostscript_bin')
    monkeypatch.setenv('GS_PATH',         '/fake/gs_path')

    # Make shutil.which return the value unchanged so the path "exists"
    monkeypatch.setattr(shutil, 'which', lambda x: x)

    result = compress_service._get_gs_cmd()
    assert result == '/fake/gs_bin'


def test_ghostscript_bin_used_when_gs_bin_absent(monkeypatch, tmp_path):
    """GHOSTSCRIPT_BIN must win when GS_BIN is not set."""
    _reset_gs_cache(monkeypatch)
    monkeypatch.delenv('GS_BIN', raising=False)
    monkeypatch.setenv('GHOSTSCRIPT_BIN', '/fake/ghostscript_bin')
    monkeypatch.setenv('GS_PATH',         '/fake/gs_path')

    monkeypatch.setattr(shutil, 'which', lambda x: x)

    result = compress_service._get_gs_cmd()
    assert result == '/fake/ghostscript_bin'


def test_gs_path_used_as_legacy_fallback(monkeypatch, tmp_path):
    """GS_PATH (legacy alias) must be used when the two canonical names are absent."""
    _reset_gs_cache(monkeypatch)
    monkeypatch.delenv('GS_BIN',          raising=False)
    monkeypatch.delenv('GHOSTSCRIPT_BIN', raising=False)
    monkeypatch.setenv('GS_PATH', '/fake/gs_path')

    monkeypatch.setattr(shutil, 'which', lambda x: x)

    result = compress_service._get_gs_cmd()
    assert result == '/fake/gs_path'


def test_env_var_not_resolvable_falls_through(monkeypatch, tmp_path):
    """If an env var points to a non-existent binary, the next candidate is tried."""
    _reset_gs_cache(monkeypatch)
    monkeypatch.setenv('GS_BIN', '/does/not/exist')
    monkeypatch.delenv('GHOSTSCRIPT_BIN', raising=False)
    monkeypatch.delenv('GS_PATH',         raising=False)

    # shutil.which returns None for the bad path but resolves 'gs' successfully
    def fake_which(name):
        if name == '/does/not/exist':
            return None
        if name == 'gs':
            return '/usr/bin/gs'
        return None

    monkeypatch.setattr(shutil, 'which', fake_which)

    result = compress_service._get_gs_cmd()
    assert result == '/usr/bin/gs'


# ── auto-detect tests ─────────────────────────────────────────────────────────

def test_autodetect_prefers_gswin64c_on_windows(monkeypatch, tmp_path):
    """Without env vars, gswin64c should be picked before gswin32c / gs."""
    _reset_gs_cache(monkeypatch)
    for var in ('GS_BIN', 'GHOSTSCRIPT_BIN', 'GS_PATH'):
        monkeypatch.delenv(var, raising=False)

    fake_paths = {
        'gswin64c': r'C:\Program Files\gs\gs10.05.0\bin\gswin64c.exe',
        'gswin32c': r'C:\Program Files\gs\gs10.05.0\bin\gswin32c.exe',
        'gs':       None,
    }
    monkeypatch.setattr(shutil, 'which', lambda name: fake_paths.get(name))

    result = compress_service._get_gs_cmd()
    assert result == fake_paths['gswin64c']


def test_autodetect_falls_back_to_gs_on_linux(monkeypatch, tmp_path):
    """Without env vars and no Windows binaries, 'gs' (Linux path) is used."""
    _reset_gs_cache(monkeypatch)
    for var in ('GS_BIN', 'GHOSTSCRIPT_BIN', 'GS_PATH'):
        monkeypatch.delenv(var, raising=False)

    def fake_which(name):
        return '/usr/bin/gs' if name == 'gs' else None

    monkeypatch.setattr(shutil, 'which', fake_which)

    result = compress_service._get_gs_cmd()
    assert result == '/usr/bin/gs'


def test_blind_fallback_when_nothing_found(monkeypatch, tmp_path):
    """If nothing resolves, the module must return the string 'gs' and not raise."""
    _reset_gs_cache(monkeypatch)
    for var in ('GS_BIN', 'GHOSTSCRIPT_BIN', 'GS_PATH'):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(shutil, 'which', lambda name: None)

    result = compress_service._get_gs_cmd()
    assert result == 'gs'


# ── cache test ────────────────────────────────────────────────────────────────

def test_result_is_cached_after_first_call(monkeypatch, tmp_path):
    """Second call must return the cached value without calling shutil.which again."""
    _reset_gs_cache(monkeypatch)
    for var in ('GS_BIN', 'GHOSTSCRIPT_BIN', 'GS_PATH'):
        monkeypatch.delenv(var, raising=False)

    call_count = {'n': 0}

    def counting_which(name):
        call_count['n'] += 1
        return '/usr/bin/gs' if name == 'gs' else None

    monkeypatch.setattr(shutil, 'which', counting_which)

    first  = compress_service._get_gs_cmd()
    second = compress_service._get_gs_cmd()

    assert first == second == '/usr/bin/gs'
    # shutil.which must NOT have been called on the second invocation
    calls_after_first = call_count['n']
    compress_service._get_gs_cmd()
    assert call_count['n'] == calls_after_first  # still the same count
