# app/utils/stats.py
# -*- coding: utf-8 -*-
"""
Métricas simples em memória para o Dashboard (/admin).

Exposto:
- track_request(path, status_code, message=None)  -> chamado no after_request do app
- record_job_event(...)                            -> compat:
      A) record_job_event(tool, ok, meta=None)
      B) record_job_event(route="...", action="...", bytes_in=..., bytes_out=..., files_out=..., ok=True)

- aggregate_stats(app=None, range_spec="1h")      -> snapshot p/ /api/admin/stats

Chaves em tools:
  "<tool>_ok" e "<tool>_err", ex.: "merge_ok", "split_err".

Observações
- Armazena séries por minuto (até 24h) para renderizar a tendência.
- Mantém uma lista (deque) das últimas falhas (status >= 400).
- NÃO grava PII; mensagens de erro são opcionais e curtas (se fornecidas).
"""

from __future__ import annotations

import os
import time
import threading
from collections import Counter, deque
from typing import Dict, Tuple, Optional, List, Any

_lock = threading.Lock()

# ------------------------
# Contadores principais
# ------------------------
_counts = Counter()      # requests_total, jobs_total, endpoint:<tool>
_by_status = Counter()   # "2xx" / "4xx" / "5xx"
_by_tool = Counter()     # compress_ok, compress_err, ...

# Série por minuto (epoch_min -> total de requisições naquele minuto)
# Mantemos o total por minuto para a tendência; jan/24h = 1440 pontos máx.
_per_min_total: Dict[int, int] = {}

# Falhas recentes (até 100)
_recent_errors: deque = deque(maxlen=100)  # items: {"time","path","status","message"}

# ------------------------
# Mapeamento de ferramentas
# ------------------------
_TOOL_PREFIX = {
    "/api/compress": "compress",
    "/api/merge":    "merge",
    "/api/split":    "split",
    "/api/convert":  "convert",
    "/api/edit":     "edit",
    "/api/organize": "organize",
}

def _tool_for_path(path: str) -> Optional[str]:
    path = (path or "").strip().lower()
    for prefix, name in _TOOL_PREFIX.items():
        if path.startswith(prefix):
            return name
    return None

# ------------------------
# Helpers de tempo/séries
# ------------------------
def _now_epoch_min() -> int:
    """Retorna o timestamp 'arredondado' para minutos (epoch // 60)."""
    return int(time.time() // 60)

def _fmt_hhmm(epoch_min: int) -> str:
    """Formata um epoch_min para HH:MM (hora local)."""
    ts = int(epoch_min) * 60
    lt = time.localtime(ts)
    return time.strftime("%H:%M", lt)

def _fmt_hhmmss(epoch_sec: int) -> str:
    lt = time.localtime(int(epoch_sec))
    return time.strftime("%H:%M:%S", lt)

def _trim_old_minutes(keep_minutes: int = 1440) -> None:
    """Remove buckets antigos para manter memória sob controle (default 24h)."""
    cutoff = _now_epoch_min() - int(keep_minutes)
    old_keys = [k for k in _per_min_total.keys() if k < cutoff]
    for k in old_keys:
        _per_min_total.pop(k, None)

def _parse_range(range_spec: str | int | None) -> int:
    """
    Converte '15m'/'1h'/'24h' ou int para quantidade de minutos.
    Defaults: '1h' (60 minutos).
    """
    if range_spec is None:
        return 60
    if isinstance(range_spec, int):
        return max(1, min(range_spec, 1440))
    s = str(range_spec).strip().lower()
    if s.endswith("m"):
        try:
            return max(1, min(int(s[:-1]), 1440))
        except Exception:
            return 60
    if s.endswith("h"):
        try:
            return max(1, min(int(s[:-1]) * 60, 1440))
        except Exception:
            return 60
    # número "cru" em minutos
    try:
        return max(1, min(int(s), 1440))
    except Exception:
        return 60

# ------------------------
# API pública de contagem
# ------------------------
def track_request(path: str, status_code: int, message: Optional[str] = None) -> None:
    """
    Contabiliza todas as requisições (chamado pelo after_request do app).
    - Soma requests_total
    - Consolida por categoria (2xx/4xx/5xx)
    - Enquadra por ferramenta com _TOOL_PREFIX
    - Atualiza bucket por minuto para timeseries
    - Registra falhas recentes (>= 400) em _recent_errors
    """
    try:
        code = int(status_code)
    except Exception:
        code = 0

    cat = (
        "2xx" if 200 <= code < 300 else
        ("4xx" if 400 <= code < 500 else
         ("5xx" if code >= 500 else "other"))
    )
    tool = _tool_for_path(path)
    epoch_min = _now_epoch_min()

    with _lock:
        # totais
        _counts["requests_total"] += 1
        _by_status[cat] += 1

        # por ferramenta
        if tool:
            key = f"{tool}_ok" if code < 400 else f"{tool}_err"
            _by_tool[key] += 1
            _counts[f"endpoint:{tool}"] += 1

        # timeseries (total por minuto)
        _per_min_total[epoch_min] = _per_min_total.get(epoch_min, 0) + 1
        _trim_old_minutes(keep_minutes=1440)

        # falhas recentes
        if code >= 400:
            _recent_errors.appendleft({
                "time": _fmt_hhmmss(int(time.time())),
                "path": path,
                "status": code,
                "message": (str(message)[:200] if message else None),
            })

def _inc_tool(tool: str, ok: bool) -> None:
    t = (tool or "").strip().lower()
    if not t:
        return
    with _lock:
        _counts["jobs_total"] += 1
        _by_tool[f"{t}_ok" if ok else f"{t}_err"] += 1
        _counts[f"endpoint:{t}"] += 1

def record_job_event(*args: Any, **kwargs: Any) -> None:
    """
    Compat de assinaturas:
      A) record_job_event(tool, ok, meta=None)
      B) record_job_event(route="...", action="...", bytes_in=..., bytes_out=..., files_out=..., ok=True)

    Ignoramos bytes_* aqui (dashboard atual não plota tamanho); mantemos apenas contagem por ferramenta.
    """
    # Caso A: (tool, ok, meta?)
    if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], bool):
        tool = args[0]
        ok = bool(args[1])
        _inc_tool(tool, ok)
        return

    # Caso B: keywords (route/action/...)
    route = kwargs.get("route")
    action = kwargs.get("action")
    ok_kw = kwargs.get("ok", True)
    if isinstance(route, str) and isinstance(action, str):
        tool = _tool_for_path(route) or (action.split("_", 1)[0].split("-", 1)[0])
        _inc_tool(tool, bool(ok_kw))
        return

    # Fallback: nada a fazer (silencioso)
    return

# ------------------------
# Uploads
# ------------------------
def _folder_usage(folder: str) -> Tuple[int, int]:
    """Conta arquivos e soma bytes do diretório (não recursivo)."""
    try:
        items = os.listdir(folder)
    except Exception:
        return 0, 0
    total = 0
    count = 0
    for name in items:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            count += 1
            try:
                total += os.path.getsize(p)
            except Exception:
                pass
    return count, total

# ------------------------
# Snapshot p/ API
# ------------------------
def _build_timeseries(range_minutes: int) -> List[Dict[str, int | str]]:
    """
    Constrói lista ordenada com os últimos N minutos:
    [{ "ts":"HH:MM", "count":N }, ...]
    """
    now_min = _now_epoch_min()
    start = now_min - (range_minutes - 1)
    out: List[Dict[str, int | str]] = []
    with _lock:
        for m in range(start, now_min + 1):
            out.append({"ts": _fmt_hhmm(m), "count": int(_per_min_total.get(m, 0))})
    return out

def aggregate_stats(app=None, range_spec: str | int = "1h") -> Dict:
    """
    Gera snapshot para o endpoint /api/admin/stats.
    range_spec: "15m" | "1h" | "24h" | <int minutos>  (default: "1h")
    """
    minutes = _parse_range(range_spec)

    uploads = ""
    if app is not None:
        uploads = app.config.get("UPLOAD_FOLDER") or uploads
    uploads = uploads or (os.path.join(os.getcwd(), "uploads"))
    files, bytes_ = _folder_usage(uploads)

    # Info do app (versão/ambiente/build) – útil para badges no topo.
    app_info = {
        "version": (getattr(app, "config", {}) or {}).get("APP_VERSION") if app else None,
        "env": os.environ.get("FLASK_ENV") or (getattr(app, "config", {}) or {}).get("ENV_NAME"),
        "build": (getattr(app, "config", {}) or {}).get("BUILD_TAG") if app else None,
    }

    with _lock:
        status = dict(_by_status)
        tools = dict(_by_tool)
        req_total = int(_counts.get("requests_total", 0))
        jobs_total = int(_counts.get("jobs_total", 0))
        recent = list(_recent_errors)  # já está em ordem decrescente

    return {
        "requests_total": req_total,
        "jobs_total": jobs_total,
        "status": status,              # {"2xx":N, "4xx":N, "5xx":N}
        "tools": tools,                # {"merge_ok":N, "merge_err":N, ...}
        "uploads": {
            "folder": uploads,
            "files": files,
            "bytes": bytes_,
        },
        "timeseries": {
            "requests_per_min": _build_timeseries(minutes)
        },
        "recent_errors": recent[:5],   # o front já limita/oculta se vazio
        "app": app_info,
    }