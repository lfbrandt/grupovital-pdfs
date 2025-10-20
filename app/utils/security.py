# -*- coding: utf-8 -*-
"""
Segurança central (Talisman + headers).
- Em HTTPS (FORCE_HTTPS=1): cabeçalhos fortes, COOP/CORP ativos.
- Em HTTP  (FORCE_HTTPS=0): NÃO enviar COOP/COEP/CORP (evita warning em HTTP).
- CSP com nonce; sem inline em produção.
"""
from __future__ import annotations
import os
from flask import Flask
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