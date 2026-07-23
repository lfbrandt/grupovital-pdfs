# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import current_app, g, has_request_context

VALID_FEEDBACK_TYPES = {"problema", "sugestao", "duvida"}
MAX_PAGE_LENGTH = 80
MIN_MESSAGE_LENGTH = 5
MAX_MESSAGE_LENGTH = 2000

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_write_lock = threading.Lock()


class FeedbackValidationError(ValueError):
    """Erro esperado de validação do feedback enviado pelo usuário."""


class FeedbackStorageError(RuntimeError):
    """Erro genérico de armazenamento sem expor detalhes internos."""


def _clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = _CONTROL_CHARS_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _safe_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_feedback_file(*, create_directory: bool = True) -> Path:
    configured_dir = (current_app.config.get("FEEDBACK_DIR") or os.getenv("FEEDBACK_DIR") or "").strip()
    base_dir = Path(configured_dir) if configured_dir else Path(current_app.instance_path) / "feedback"
    base_dir = base_dir.expanduser()

    try:
        resolved_base = base_dir.resolve()
        forbidden_dirs = [
            Path(current_app.static_folder).resolve(),
            (Path(current_app.root_path) / current_app.template_folder).resolve(),
            Path(current_app.config.get("UPLOAD_FOLDER", "")).resolve(),
        ]
        if any(_safe_relative_to(resolved_base, forbidden) for forbidden in forbidden_dirs):
            current_app.logger.error("Feedback storage directory rejected.")
            raise FeedbackStorageError("Diretório de feedback inseguro.")

        if create_directory:
            resolved_base.mkdir(parents=True, exist_ok=True)
        return resolved_base / "feedback.jsonl"
    except FeedbackStorageError:
        raise
    except Exception as exc:
        current_app.logger.error("Feedback storage directory unavailable: %s", exc.__class__.__name__)
        raise FeedbackStorageError("Não foi possível preparar o armazenamento.") from exc


def _normalize_type(value: Any) -> str:
    feedback_type = _clean_text(value).lower()
    if feedback_type not in VALID_FEEDBACK_TYPES:
        raise FeedbackValidationError("Tipo de feedback inválido.")
    return feedback_type


def _normalize_page(value: Any) -> str:
    page = _clean_text(value) or "desconhecida"
    return page[:MAX_PAGE_LENGTH]


def _normalize_message(value: Any) -> str:
    message = _clean_text(value)
    if not message:
        raise FeedbackValidationError("Mensagem obrigatória.")
    if len(message) < MIN_MESSAGE_LENGTH:
        raise FeedbackValidationError("Mensagem muito curta.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise FeedbackValidationError("Mensagem muito longa.")
    return message


def build_feedback_record(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FeedbackValidationError("Envie um JSON válido.")

    request_id = getattr(g, "request_id", None) if has_request_context() else None
    if not request_id:
        request_id = secrets.token_hex(4)

    return {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "page": _normalize_page(payload.get("page")),
        "type": _normalize_type(payload.get("type")),
        "message": _normalize_message(payload.get("message")),
        "app_version": current_app.config.get("APP_VERSION", ""),
        "request_id": request_id,
    }


def save_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    record = build_feedback_record(payload)
    feedback_file = _resolve_feedback_file()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))

    try:
        with _write_lock:
            with feedback_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as exc:
        current_app.logger.error("Feedback write failed: %s", exc.__class__.__name__)
        raise FeedbackStorageError("Não foi possível salvar o feedback.") from exc

    return record


def _public_feedback_record(record: dict[str, Any]) -> dict[str, str]:
    return {
        "timestamp": _clean_text(record.get("ts") or record.get("timestamp"))[:64],
        "page": _clean_text(record.get("page"))[:MAX_PAGE_LENGTH],
        "type": _clean_text(record.get("type"))[:32],
        "message": _clean_text(record.get("message"))[:MAX_MESSAGE_LENGTH],
        "app_version": _clean_text(record.get("app_version"))[:120],
        "request_id": _clean_text(record.get("request_id"))[:64],
    }


def list_feedback(limit: int = 50) -> list[dict[str, str]]:
    """Retorna os registros válidos mais recentes sem expor o arquivo de origem."""
    safe_limit = max(1, min(int(limit), 100))
    feedback_file = _resolve_feedback_file(create_directory=False)
    recent: deque[dict[str, str]] = deque(maxlen=safe_limit)

    try:
        with _write_lock:
            with feedback_file.open("r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        continue
                    if isinstance(record, dict):
                        public_record = _public_feedback_record(record)
                        if (
                            not public_record["message"]
                            or public_record["type"] not in VALID_FEEDBACK_TYPES
                        ):
                            continue
                        recent.append(public_record)
    except FileNotFoundError:
        return []
    except Exception as exc:
        current_app.logger.error("Feedback read failed: %s", exc.__class__.__name__)
        raise FeedbackStorageError("Não foi possível consultar os feedbacks.") from exc

    return list(reversed(recent))
