# -*- coding: utf-8 -*-
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import limiter
from ..services.feedback_service import (
    FeedbackStorageError,
    FeedbackValidationError,
    save_feedback,
)

feedback_bp = Blueprint("feedback_bp", __name__)


@feedback_bp.post("/api/feedback")
@limiter.limit("5 per minute")
def submit_feedback():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Envie um JSON válido."}), 400

    try:
        record = save_feedback(payload)
    except FeedbackValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except FeedbackStorageError:
        return jsonify({"error": "Não foi possível salvar o feedback agora."}), 500

    return jsonify({"ok": True, "request_id": record["request_id"]}), 201
