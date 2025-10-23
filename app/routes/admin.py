# app/routes/admin.py
# -*- coding: utf-8 -*-
"""
Dashboard de admin (/admin) e APIs (/api/admin/*).
Todas as chamadas de API exigem o header: X-Admin-Token.
"""

from __future__ import annotations
import os
from collections import deque
from typing import List, Dict

from flask import (
    Blueprint, current_app, render_template,
    request, jsonify, abort
)
from werkzeug.exceptions import BadRequest

from .. import limiter

# ------------------ Blueprints ------------------
# Página do dashboard
admin_bp = Blueprint("admin_bp", __name__)

# APIs do dashboard
admin_api_bp = Blueprint("admin_api_bp", __name__, url_prefix="/api/admin")

# ------------------ Helpers ------------------
def _require_admin():
    cfg = (current_app.config.get("ADMIN_TOKEN") or "").strip()
    tok = (request.headers.get("X-Admin-Token") or request.args.get("token") or "").strip()
    if not cfg:
        abort(404)  # dashboard desabilitado se não houver token configurado
    if tok != cfg:
        abort(401, description="Admin token inválido")

def _app_meta():
    return {
        "version": current_app.config.get("APP_VERSION", "-"),
        "env": os.environ.get("FLASK_ENV", "development"),
        "build": current_app.config.get("BUILD_TAG", current_app.config.get("APP_VERSION", "-")),
    }

def _tail_file(path: str, n: int) -> List[str]:
    dq: deque[str] = deque(maxlen=min(max(n, 1), 5000))
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                dq.append(line.rstrip("\n"))
    except FileNotFoundError:
        return []
    return list(dq)

def _parse_log_line(line: str) -> Dict[str, str]:
    # Formato: "%(asctime)s | %(levelname)-5s | %(name)s:%(funcName)s:%(lineno)d | req=%(request_id)s | %(message)s"
    parts = line.split(" | ", 4)
    return {
        "ts":   parts[0] if len(parts) > 0 else "",
        "level": (parts[1] or "").strip() if len(parts) > 1 else "",
        "where": (parts[2] or "").strip() if len(parts) > 2 else "",
        "req":   (parts[3] or "").strip() if len(parts) > 3 else "",
        "msg":   (parts[4] or "").strip() if len(parts) > 4 else "",
        "raw":   line,
    }

# ------------------ Rotas ------------------
# Página HTML
@admin_bp.get("/admin")
def admin_page():
    if not (current_app.config.get("ADMIN_TOKEN") or "").strip():
        abort(404)
    return render_template("admin.html")

# Snapshot de métricas
@admin_api_bp.get("/stats")
@limiter.limit("30 per minute")
def api_stats():
    _require_admin()
    # Import tardio para evitar ciclos
    from ..utils.stats import aggregate_stats
    data = aggregate_stats(current_app)
    data["app"] = _app_meta()
    data.setdefault("timeseries", [])
    data.setdefault("recent_errors", [])
    return jsonify(data)

# Últimas linhas do log
@admin_api_bp.get("/logs")
@limiter.limit("10 per minute")
def api_logs():
    _require_admin()
    try:
        tail = int(request.args.get("tail", "400"))
    except ValueError:
        raise BadRequest("tail inválido")
    tail = max(1, min(tail, 5000))

    level = (request.args.get("level") or "").strip().upper()
    if level and level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise BadRequest("level inválido")

    log_path = os.path.join(current_app.root_path, "app.log")
    lines = _tail_file(log_path, tail)

    items = []
    for ln in lines:
        row = _parse_log_line(ln)
        if not level or row.get("level", "").upper() == level:
            items.append(row)

    return jsonify({
        "app": _app_meta(),
        "file": log_path,
        "count": len(items),
        "items": items,
    })

# Gerar uma linha de log via dashboard (útil para depuração)
@admin_api_bp.post("/log")
@limiter.limit("10 per minute")
def api_log_write():
    _require_admin()
    data = request.get_json(silent=True) or {}
    level = (data.get("level") or "INFO").upper().strip()
    message = (data.get("message") or "").strip()
    if not message:
        raise BadRequest("message obrigatório")

    fn = {
        "DEBUG": current_app.logger.debug,
        "INFO": current_app.logger.info,
        "WARNING": current_app.logger.warning,
        "ERROR": current_app.logger.error,
        "CRITICAL": current_app.logger.critical,
    }.get(level, current_app.logger.info)

    fn(f"[ADMIN] {message}")
    return jsonify(ok=True, level=level)