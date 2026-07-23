# app/utils/stats.py
# -*- coding: utf-8 -*-
"""
Métricas simples em memória para o Dashboard (/admin).

Exposto:
- track_request(path, status_code, message=None)  -> chamado no after_request do app
- record_job_event(...)                            -> compat:
      A) record_job_event(tool, ok, meta=None)
      B) record_job_event(route="...", action="...", bytes_in=..., bytes_out=..., files_out=..., ok=True)

- aggregate_stats(app=None, range_spec="15m")     -> snapshot p/ /api/admin/stats

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
from datetime import datetime, timezone
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

# Eventos com timestamp UTC usados pelos recortes de 15m, 1h e 24h.
# O armazenamento continua exclusivamente em memória.
_metric_events: deque = deque()

_RANGE_MINUTES = {
    "15m": 15,
    "1h": 60,
    "24h": 1440,
}

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

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(
                f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
            )
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _trim_old_minutes(keep_minutes: int = 1440) -> None:
    """Remove buckets antigos para manter memória sob controle (default 24h)."""
    cutoff = _now_epoch_min() - int(keep_minutes)
    old_keys = [k for k in _per_min_total.keys() if k < cutoff]
    for k in old_keys:
        _per_min_total.pop(k, None)

def _trim_old_events(now: datetime) -> None:
    cutoff = now.timestamp() - (24 * 60 * 60)
    while _metric_events:
        event = _metric_events[0]
        if not isinstance(event, dict):
            _metric_events.popleft()
            continue
        timestamp = _parse_timestamp(event.get("timestamp") or event.get("ts"))
        if timestamp is None or timestamp.timestamp() < cutoff:
            _metric_events.popleft()
            continue
        break

def _parse_range(range_spec: str | None) -> Tuple[str, int]:
    """
    Valida o período suportado e retorna (identificador, minutos).
    O padrão é 15m.
    """
    normalized = "15m" if range_spec is None else str(range_spec).strip().lower()
    if not normalized:
        normalized = "15m"
    if normalized not in _RANGE_MINUTES:
        raise ValueError("unsupported stats range")
    return normalized, _RANGE_MINUTES[normalized]

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
    now = _utc_now()
    epoch_min = int(now.timestamp() // 60)

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

        _metric_events.append({
            "timestamp": _format_timestamp(now),
            "kind": "request",
            "path": path,
            "status": code,
            "message": (str(message)[:200] if message else None),
        })
        _trim_old_events(now)

def _inc_tool(tool: str, ok: bool) -> None:
    t = (tool or "").strip().lower()
    if not t:
        return
    now = _utc_now()
    with _lock:
        _counts["jobs_total"] += 1
        _by_tool[f"{t}_ok" if ok else f"{t}_err"] += 1
        _counts[f"endpoint:{t}"] += 1
        _metric_events.append({
            "timestamp": _format_timestamp(now),
            "kind": "job",
            "tool": t,
            "ok": bool(ok),
        })
        _trim_old_events(now)

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
def _build_timeseries(
    events: List[Tuple[Dict[str, Any], datetime]],
    range_minutes: int,
    now: datetime,
) -> List[Dict[str, int | str]]:
    """
    Constrói lista ordenada com os últimos N minutos:
    [{ "ts":"HH:MM", "count":N }, ...]
    """
    now_min = int(now.timestamp() // 60)
    start = now_min - (range_minutes - 1)
    counts = Counter(
        int(timestamp.timestamp() // 60)
        for event, timestamp in events
        if event.get("kind") == "request"
    )
    out: List[Dict[str, int | str]] = []
    for minute in range(start, now_min + 1):
        out.append({"ts": _fmt_hhmm(minute), "count": int(counts.get(minute, 0))})
    return out

def aggregate_stats(app=None, range_spec: str | None = "15m") -> Dict:
    """
    Gera snapshot para o endpoint /api/admin/stats.
    range_spec: "15m" | "1h" | "24h" (default: "15m")
    """
    selected_range, minutes = _parse_range(range_spec)
    now = _utc_now()
    now_min = int(now.timestamp() // 60)
    start_min = now_min - (minutes - 1)
    range_start = datetime.fromtimestamp(start_min * 60, tz=timezone.utc)

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
        _trim_old_events(now)
        events = []
        for event in _metric_events:
            if not isinstance(event, dict):
                continue
            timestamp = _parse_timestamp(event.get("timestamp") or event.get("ts"))
            if timestamp is None or timestamp < range_start or timestamp > now:
                continue
            events.append((dict(event), timestamp))

    status = Counter()
    tools = Counter()
    recent = []
    req_total = 0
    jobs_total = 0

    for event, timestamp in events:
        kind = event.get("kind")
        if kind == "job":
            tool = str(event.get("tool") or "").strip().lower()
            if tool:
                jobs_total += 1
                tools[f"{tool}_ok" if bool(event.get("ok")) else f"{tool}_err"] += 1
            continue
        if kind != "request":
            continue

        try:
            code = int(event.get("status"))
        except (TypeError, ValueError):
            code = 0
        category = (
            "2xx" if 200 <= code < 300 else
            ("4xx" if 400 <= code < 500 else
             ("5xx" if code >= 500 else "other"))
        )
        req_total += 1
        status[category] += 1

        path = str(event.get("path") or "")
        tool = _tool_for_path(path)
        if tool:
            tools[f"{tool}_ok" if code < 400 else f"{tool}_err"] += 1

        if code >= 400:
            recent.append({
                "timestamp": timestamp,
                "time": _fmt_hhmmss(int(timestamp.timestamp())),
                "path": path,
                "status": code,
                "message": (
                    str(event.get("message"))[:200]
                    if event.get("message")
                    else None
                ),
            })

    recent.sort(key=lambda item: item["timestamp"], reverse=True)
    recent_output = [
        {key: value for key, value in item.items() if key != "timestamp"}
        for item in recent[:5]
    ]

    return {
        "range": selected_range,
        "requests_total": req_total,
        "jobs_total": jobs_total,
        "status": dict(status),         # {"2xx":N, "4xx":N, "5xx":N}
        "tools": dict(tools),           # {"merge_ok":N, "merge_err":N, ...}
        "uploads": {
            "files": files,
            "bytes": bytes_,
        },
        "timeseries": {
            "requests_per_min": _build_timeseries(events, minutes, now)
        },
        "recent_errors": recent_output,
        "app": app_info,
    }
