# -*- coding: utf-8 -*-
"""
Segurança central (Talisman + headers).
- Em HTTPS (FORCE_HTTPS=1): cabeçalhos fortes, COOP/CORP ativos.
- Em HTTP  (FORCE_HTTPS=0): NÃO enviar COOP/COEP/CORP (evita warning em HTTP).
- CSP com nonce; sem inline em produção.
"""
from __future__ import annotations
import hmac
import os
import re
import secrets
from urllib.parse import unquote

from flask import Flask, session
from flask_talisman import Talisman

# Reexport de helpers de MIME para compatibilidade com código legado
# (assim não quebra quem fazia: from app.utils.security import sanitize_filename, etc.)
from .mime import (  # noqa: F401
    sanitize_filename,
    detect_mime,
    detect_mime_from_buffer,
    detect_mime_or_ext,
    is_allowed_mime,
)

DEFAULT_CSP = {
    "default-src": "'self'",
    "script-src": ["'self'", "https://cdn.jsdelivr.net"],
    "style-src": ["'self'", "https://fonts.googleapis.com"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'", "https://fonts.gstatic.com"],
    "connect-src": ["'self'", "blob:"],
    "worker-src": ["'self'", "blob:"],
    "frame-src": ["'self'", "blob:"],
    "object-src": "'none'",
    "base-uri": "'self'",
}

OUTPUT_OWNER_SESSION_KEY = "output_owner_id"
GENERATED_OUTPUTS_DIR = "generated"
EDIT_SESSIONS_DIR = "edit_sessions"
_OUTPUT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def is_valid_output_id(value: object) -> bool:
    return isinstance(value, str) and _OUTPUT_ID_RE.fullmatch(value) is not None


def get_or_create_output_owner_id() -> str:
    owner_id = session.get(OUTPUT_OWNER_SESSION_KEY)
    if not is_valid_output_id(owner_id):
        owner_id = secrets.token_hex(16)
        session[OUTPUT_OWNER_SESSION_KEY] = owner_id
    return owner_id


def current_output_owner_id() -> str | None:
    owner_id = session.get(OUTPUT_OWNER_SESSION_KEY)
    return owner_id if is_valid_output_id(owner_id) else None


def generate_output_job_id() -> str:
    return secrets.token_hex(16)


def generate_edit_session_id() -> str:
    return secrets.token_hex(16)


def is_valid_edit_session_id(value: object) -> bool:
    return is_valid_output_id(value)


def _real_abs(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.fspath(path)))


def _is_within_dir(parent: str, child: str) -> bool:
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def make_session_output_dir(upload_folder: str) -> str:
    base = os.path.abspath(upload_folder)
    owner_id = get_or_create_output_owner_id()
    for _ in range(8):
        job_id = generate_output_job_id()
        output_dir = os.path.join(base, GENERATED_OUTPUTS_DIR, owner_id, job_id)
        try:
            os.makedirs(output_dir, mode=0o700, exist_ok=False)
            return output_dir
        except FileExistsError:
            continue
    raise RuntimeError("Nao foi possivel alocar diretorio de saida.")


def make_edit_session_dir(upload_folder: str) -> tuple[str, str]:
    upload_base = _real_abs(upload_folder)
    edit_root = _real_abs(os.path.join(upload_base, EDIT_SESSIONS_DIR))
    if not _is_within_dir(upload_base, edit_root):
        raise RuntimeError("Diretorio de sessoes de edicao invalido.")

    os.makedirs(edit_root, mode=0o700, exist_ok=True)
    edit_root = _real_abs(edit_root)
    if not _is_within_dir(upload_base, edit_root):
        raise RuntimeError("Diretorio de sessoes de edicao invalido.")

    owner_id = get_or_create_output_owner_id()
    owner_root = _real_abs(os.path.join(edit_root, owner_id))
    if not _is_within_dir(edit_root, owner_root):
        raise RuntimeError("Diretorio proprietario de edicao invalido.")
    os.makedirs(owner_root, mode=0o700, exist_ok=True)

    for _ in range(8):
        edit_session_id = generate_edit_session_id()
        session_dir = _real_abs(os.path.join(owner_root, edit_session_id))
        if not _is_within_dir(owner_root, session_dir):
            continue
        try:
            os.makedirs(session_dir, mode=0o700, exist_ok=False)
            return edit_session_id, session_dir
        except FileExistsError:
            continue
    raise RuntimeError("Nao foi possivel alocar sessao de edicao.")


def resolve_owned_edit_session_dir(upload_folder: str, edit_session_id: str) -> str | None:
    if not is_valid_edit_session_id(edit_session_id):
        return None

    owner_id = current_output_owner_id()
    if not owner_id:
        return None

    upload_base = _real_abs(upload_folder)
    edit_root = _real_abs(os.path.join(upload_base, EDIT_SESSIONS_DIR))
    if not _is_within_dir(upload_base, edit_root):
        return None

    owner_root = _real_abs(os.path.join(edit_root, owner_id))
    if not _is_within_dir(edit_root, owner_root):
        return None

    session_dir = _real_abs(os.path.join(owner_root, edit_session_id))
    if not _is_within_dir(owner_root, session_dir):
        return None
    if not os.path.isdir(session_dir):
        return None
    return session_dir


def normalize_generated_output_rel_path(rel_path: str) -> str | None:
    if not isinstance(rel_path, str) or not rel_path:
        return None
    try:
        decoded = unquote(rel_path)
    except Exception:
        return None
    decoded = decoded.replace("\\", "/")
    if decoded != decoded.strip() or decoded.startswith("/"):
        return None

    parts = decoded.split("/")
    if len(parts) != 4 or any(part in {"", ".", ".."} for part in parts):
        return None

    namespace, owner_id, job_id, filename = parts
    if namespace != GENERATED_OUTPUTS_DIR:
        return None
    if not is_valid_output_id(owner_id) or not is_valid_output_id(job_id):
        return None
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return None
    return "/".join(parts)


def session_owned_generated_rel_path(rel_path: str) -> str | None:
    normalized = normalize_generated_output_rel_path(rel_path)
    if not normalized:
        return None

    owner_id = normalized.split("/", 3)[1]
    session_owner = current_output_owner_id()
    if not session_owner or not hmac.compare_digest(session_owner, owner_id):
        return None
    return normalized

def _is_true(v: str | None) -> bool:
    return str(v or "").lower() in {"1", "true", "yes", "on"}

def init_security(app: Flask) -> None:
    force_https = _is_true(os.getenv("FORCE_HTTPS", "0"))

    talisman_kwargs = dict(
        content_security_policy=DEFAULT_CSP,
        content_security_policy_nonce_in=["script-src", "style-src"],
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        permissions_policy={"browsing-topics": "()"},
        force_https=force_https,
        strict_transport_security=force_https,
        session_cookie_secure=force_https,
    )

    # Em HTTPS → isolamento básico sem COEP (para não quebrar o pdf.js)
    if force_https:
        talisman_kwargs.update(
            cross_origin_opener_policy="same-origin",
            cross_origin_resource_policy="same-origin",
            cross_origin_embedder_policy=None,
        )
    else:
        # Em HTTP desligamos todos (não gera warning no DevTools)
        talisman_kwargs.update(
            cross_origin_opener_policy=None,
            cross_origin_resource_policy=None,
            cross_origin_embedder_policy=None,
        )

    Talisman(app, **talisman_kwargs)

    # Fallback: se algum middleware reintroduzir os headers, limpa em HTTP.
    @app.after_request
    def _strip_isolation_headers(resp):
        if not force_https:
            for h in (
                "Cross-Origin-Opener-Policy",
                "Cross-Origin-Embedder-Policy",
                "Cross-Origin-Resource-Policy",
            ):
                resp.headers.pop(h, None)
        return resp
