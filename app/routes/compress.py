import hmac
import os
import re
import secrets
import time
import uuid
import base64
import json
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from flask import Blueprint, request, jsonify, send_file, current_app, session
from werkzeug.exceptions import BadRequest

if os.name == "nt":
    import msvcrt
else:
    import fcntl

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter

from PIL import Image
from io import BytesIO

from ..services.compress_service import (
    comprimir_pdf,
    comprimir_pdf_com_params,
    USER_PROFILES,
    _get_ghostscript_cmd,
    build_selected_baseline,
    count_pdf_pages,
    enrich_page_analysis,
    GHOSTSCRIPT_TIMEOUT,
    MIN_SAFE_DPI,
    MIN_COMPRESSION_GAIN_RATIO,
    MAX_TARGET_COMPRESSION_ATTEMPTS,
    TARGET_GRAYSCALE_PROFILE,
    TARGET_JPEG_RECOMPRESSION_PROFILE,
    TARGET_SIZE_PROFILES,
    run_ghostscript_command,
    validate_compressed_pdf,
    validate_pdf_readable,
)
from ..services.sanitize_service import sanitize_pdf_preserving_content
from ..utils.config_utils import ensure_upload_folder_exists, validate_upload
from ..utils.pdf_utils import (
    cleanup_upload_files,
    pdf_preservation_warnings,
    pdf_requires_content_preservation,
    replace_pdf_pages_preserving_catalog,
    register_response_file_cleanup,
)
from ..utils.limits import enforce_pdf_page_limit, get_max_pdf_pages
from .. import limiter

# ── Sessões de análise — armazenamento em disco ───────────────────────────────
# _ANALYSE_SESSIONS (dict em memória) quebra com Gunicorn multi-worker porque
# cada worker tem seu próprio espaço de memória. A request de analyze pode cair
# no Worker A e a de process-with-settings no Worker B → KeyError → HTTP 404.
#
# Solução: gravar o mapeamento analyse_id → filepath em um arquivo .session
# no próprio UPLOAD_FOLDER. Todos os workers leem/escrevem o mesmo disco.
# TTL é enforçado na leitura (_session_get) e na limpeza periódica (_purge).
_SESSION_TTL_SECONDS: int = 3600  # 1 hora
_PROCESS_LOCK_TTL_SECONDS: int = 3600
_COMPRESS_OWNER_SESSION_KEY = "compress_owner_id"
_ANALYSE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_OWNER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_COMPRESSION_MODES = {"manual", "target_size"}
_TARGET_SIZE_MARGIN = Decimal("0.99")
_MIN_TARGET_SIZE_MB = Decimal("0.20")
_MAX_TARGET_SIZE_MB = Decimal("50")
_DEFAULT_TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC = 90
_MIN_TARGET_ATTEMPT_REMAINING_SEC = 1
_target_clock = time.monotonic


def _process_with_settings_rate_limit() -> str:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict) and payload.get("mode") == "target_size":
        return "2 per minute"
    return "5 per minute"


def _target_compression_total_timeout_sec() -> int:
    raw_value = current_app.config.get(
        "TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC",
        os.environ.get(
            "TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC",
            _DEFAULT_TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC,
        ),
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = _DEFAULT_TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC
    return value if value >= 1 else _DEFAULT_TARGET_COMPRESSION_TOTAL_TIMEOUT_SEC


def _session_path(analyse_id: str, upload_folder: str) -> str:
    return os.path.join(upload_folder, f".session_{analyse_id}")


def _process_lock_path(analyse_id: str, upload_folder: str) -> str:
    return os.path.join(upload_folder, f".compress_lock_{analyse_id}")


def _is_within_upload_folder(path: str, upload_folder: str) -> bool:
    try:
        base = os.path.normcase(os.path.realpath(upload_folder))
        target = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([base, target]) == base
    except (OSError, ValueError):
        return False


def _current_compress_owner_id() -> str | None:
    owner_id = session.get(_COMPRESS_OWNER_SESSION_KEY)
    if isinstance(owner_id, str) and _OWNER_ID_RE.fullmatch(owner_id):
        return owner_id
    return None


def _get_or_create_compress_owner_id() -> str:
    owner_id = _current_compress_owner_id()
    if owner_id:
        return owner_id
    owner_id = secrets.token_urlsafe(32)
    session[_COMPRESS_OWNER_SESSION_KEY] = owner_id
    return owner_id


def _session_set(
    analyse_id: str,
    pdf_path: str,
    upload_folder: str,
    owner_id: str,
    *,
    uploaded_size_bytes: int | None = None,
) -> None:
    """Persiste metadados da análise de forma atômica entre workers."""
    if not _ANALYSE_ID_RE.fullmatch(analyse_id):
        raise ValueError("invalid_analyse_id")
    if not _OWNER_ID_RE.fullmatch(owner_id):
        raise ValueError("invalid_owner_id")
    if not _is_within_upload_folder(pdf_path, upload_folder):
        raise ValueError("session_path_outside_uploads")

    created_at = time.time()
    if (
        not isinstance(uploaded_size_bytes, int)
        or isinstance(uploaded_size_bytes, bool)
        or uploaded_size_bytes <= 0
    ):
        uploaded_size_bytes = os.path.getsize(pdf_path)
    data = {
        "owner_id": owner_id,
        "analyse_id": analyse_id,
        "path": os.path.realpath(pdf_path),
        "uploaded_size_bytes": uploaded_size_bytes,
        "created_at": created_at,
        "expires_at": created_at + _SESSION_TTL_SECONDS,
    }
    sess_file = _session_path(analyse_id, upload_folder)
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=upload_folder,
            prefix=".session_tmp_",
            delete=False,
        ) as handle:
            temp_file = handle.name
            json.dump(data, handle, separators=(",", ":"))
        os.replace(temp_file, sess_file)
    except OSError as exc:
        current_app.logger.error(
            "[session] falha ao gravar sessao: %s", type(exc).__name__
        )
        raise
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


def _session_delete(analyse_id: str, upload_folder: str) -> None:
    try:
        os.remove(_session_path(analyse_id, upload_folder))
    except OSError:
        pass


def _cleanup_session_record(
    analyse_id: str,
    upload_folder: str,
    data: dict | None = None,
) -> None:
    _session_delete(analyse_id, upload_folder)
    path = data.get("path", "") if isinstance(data, dict) else ""
    if (
        isinstance(path, str)
        and path
        and _is_within_upload_folder(path, upload_folder)
        and os.path.exists(path)
    ):
        try:
            os.remove(path)
        except OSError:
            pass


def _session_get_details(analyse_id: str) -> dict | None:
    """Retorna metadados apenas ao proprietário atual e sem consumir a análise."""
    if not isinstance(analyse_id, str) or not _ANALYSE_ID_RE.fullmatch(analyse_id):
        return None
    owner_id = _current_compress_owner_id()
    if not owner_id:
        return None

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    sess_file = _session_path(analyse_id, upload_folder)
    try:
        with open(sess_file, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    stored_owner = data.get("owner_id")
    stored_analyse_id = data.get("analyse_id")
    if not isinstance(stored_owner, str) or not _OWNER_ID_RE.fullmatch(stored_owner):
        _cleanup_session_record(analyse_id, upload_folder, data)
        return None
    if not hmac.compare_digest(stored_owner, owner_id):
        return None
    if (
        not isinstance(stored_analyse_id, str)
        or not hmac.compare_digest(stored_analyse_id, analyse_id)
    ):
        _cleanup_session_record(analyse_id, upload_folder, data)
        return None

    try:
        created_at = float(data.get("created_at", 0))
        expires_at = float(data.get("expires_at", 0))
    except (TypeError, ValueError):
        _cleanup_session_record(analyse_id, upload_folder, data)
        return None

    path = data.get("path", "")
    if not isinstance(path, str) or not _is_within_upload_folder(path, upload_folder):
        _session_delete(analyse_id, upload_folder)
        return None
    if (
        created_at <= 0
        or expires_at <= created_at
        or expires_at - created_at > _SESSION_TTL_SECONDS + 1
        or time.time() >= expires_at
    ):
        _cleanup_session_record(analyse_id, upload_folder, data)
        return None
    if not os.path.exists(path):
        _session_delete(analyse_id, upload_folder)
        return None
    uploaded_size_bytes = data.get("uploaded_size_bytes")
    if (
        not isinstance(uploaded_size_bytes, int)
        or isinstance(uploaded_size_bytes, bool)
        or uploaded_size_bytes <= 0
    ):
        uploaded_size_bytes = os.path.getsize(path)
    return {
        "path": path,
        "uploaded_size_bytes": uploaded_size_bytes,
    }


def _session_get(analyse_id: str) -> str | None:
    details = _session_get_details(analyse_id)
    return details["path"] if details else None


def _process_lock_is_stale(lock_path: str, now: float | None = None) -> bool:
    try:
        with open(lock_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        created_at = float(data.get("created_at", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        try:
            created_at = os.path.getmtime(lock_path)
        except OSError:
            return True
    return (now or time.time()) - created_at > _PROCESS_LOCK_TTL_SECONDS


@contextmanager
def _process_lock_guard(
    analyse_id: str,
    upload_folder: str,
    *,
    blocking: bool = False,
):
    """
    Serializa somente a manutenção do lock de um analyse_id entre processos.

    O arquivo de sessão é estável durante processamentos recuperáveis, portanto
    pode proteger a sequência ler/remover/recriar do lock O_EXCL sem introduzir
    um mutex global entre análises diferentes.
    """
    session_file = _session_path(analyse_id, upload_folder)
    try:
        fd = os.open(session_file, os.O_RDWR)
    except OSError:
        yield False
        return

    acquired = False
    try:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(fd, mode, 1)
            else:
                flags = fcntl.LOCK_EX
                if not blocking:
                    flags |= fcntl.LOCK_NB
                fcntl.flock(fd, flags)
            acquired = True
        except OSError:
            yield False
            return

        yield True
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _acquire_process_lock(analyse_id: str, upload_folder: str) -> str | None:
    """Lock atômico multiprocesso; retorna token privado ou None em conflito."""
    if not _ANALYSE_ID_RE.fullmatch(analyse_id):
        return None
    lock_path = _process_lock_path(analyse_id, upload_folder)
    with _process_lock_guard(analyse_id, upload_folder) as guarded:
        if not guarded:
            return None
        for _ in range(2):
            token = secrets.token_urlsafe(24)
            try:
                fd = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"token": token, "created_at": time.time()},
                        handle,
                        separators=(",", ":"),
                    )
                return token
            except FileExistsError:
                if not _process_lock_is_stale(lock_path):
                    return None
                try:
                    os.remove(lock_path)
                except OSError:
                    return None
            except OSError:
                return None
    return None


def _remove_process_lock_if_owned(lock_path: str, token: str) -> None:
    try:
        with open(lock_path, "r", encoding="utf-8") as handle:
            stored = json.load(handle).get("token")
        if isinstance(stored, str) and hmac.compare_digest(stored, token):
            os.remove(lock_path)
    except (OSError, AttributeError, json.JSONDecodeError):
        pass


def _refresh_process_lock(
    analyse_id: str,
    upload_folder: str,
    token: str | None,
) -> bool:
    """Renova a expiração apenas quando o chamador ainda possui o lock."""
    if not token:
        return False
    lock_path = _process_lock_path(analyse_id, upload_folder)
    with _process_lock_guard(
        analyse_id,
        upload_folder,
        blocking=True,
    ) as guarded:
        if not guarded:
            return False
        try:
            with open(lock_path, "r+", encoding="utf-8") as handle:
                data = json.load(handle)
                stored = data.get("token")
                if not isinstance(stored, str) or not hmac.compare_digest(
                    stored,
                    token,
                ):
                    return False
                handle.seek(0)
                handle.truncate()
                json.dump(
                    {"token": token, "created_at": time.time()},
                    handle,
                    separators=(",", ":"),
                )
                handle.flush()
            return True
        except (OSError, AttributeError, json.JSONDecodeError):
            return False


def _release_process_lock(
    analyse_id: str,
    upload_folder: str,
    token: str | None,
) -> None:
    if not token:
        return
    lock_path = _process_lock_path(analyse_id, upload_folder)
    session_file = _session_path(analyse_id, upload_folder)
    if os.path.exists(session_file):
        with _process_lock_guard(
            analyse_id,
            upload_folder,
            blocking=True,
        ) as guarded:
            if guarded:
                _remove_process_lock_if_owned(lock_path, token)
            elif not os.path.exists(session_file):
                # Sucesso já consumiu a sessão; sem ela não há novo adquirente.
                _remove_process_lock_if_owned(lock_path, token)
        return

    # No caminho de sucesso a sessão é removida antes do finally. Como toda
    # aquisição exige essa sessão, não há processo concorrente capaz de recriar
    # o lock neste ponto.
    _remove_process_lock_if_owned(lock_path, token)


def _purge_expired_sessions() -> None:
    """Remove análises inválidas/expiradas, sem interromper processamento ativo."""
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    now = time.time()
    try:
        for fname in os.listdir(upload_folder):
            if not fname.startswith(".session_"):
                continue
            analyse_id = fname.removeprefix(".session_")
            fpath = os.path.join(upload_folder, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                expires_at = float(data.get("expires_at", 0))
                valid_record = (
                    _ANALYSE_ID_RE.fullmatch(analyse_id) is not None
                    and data.get("analyse_id") == analyse_id
                    and isinstance(data.get("owner_id"), str)
                    and _OWNER_ID_RE.fullmatch(data["owner_id"]) is not None
                    and expires_at > 0
                )
                expired = expires_at <= now
                lock_path = _process_lock_path(analyse_id, upload_folder)
                lock_active = (
                    os.path.exists(lock_path)
                    and not _process_lock_is_stale(lock_path, now)
                )
                if (not valid_record or expired) and not lock_active:
                    _cleanup_session_record(analyse_id, upload_folder, data)
                    if os.path.exists(lock_path):
                        try:
                            os.remove(lock_path)
                        except OSError:
                            pass
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                if _ANALYSE_ID_RE.fullmatch(analyse_id):
                    _cleanup_session_record(analyse_id, upload_folder)
    except OSError as exc:
        current_app.logger.warning("[session] _purge falhou: %s", type(exc).__name__)


compress_bp = Blueprint("compress", __name__, url_prefix="/api/compress")


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize_profile(p: str) -> str:
    p = (p or "").strip().lower()
    return p if p in USER_PROFILES else "equilibrio"


def _normalize_pages(pages_raw):
    if not pages_raw:
        return None
    if isinstance(pages_raw, str):
        try:
            pages_raw = json.loads(pages_raw)
        except json.JSONDecodeError:
            raise ValueError("pages/order deve ser JSON válido (lista de inteiros 1-based)")
    if pages_raw is None:
        return None
    if not isinstance(pages_raw, list):
        raise ValueError("pages/order deve ser uma lista de inteiros (1-based)")
    out = []
    for p in pages_raw:
        try:
            n = int(p)
            if n >= 1:
                out.append(n)
        except Exception:
            raise ValueError("pages/order deve conter apenas inteiros")
    return out or None


def _normalize_rotations(rot_raw):
    if rot_raw is None or rot_raw == "":
        return None
    if isinstance(rot_raw, str):
        try:
            rot_raw = json.loads(rot_raw)
        except json.JSONDecodeError:
            raise ValueError("rotations/rot deve ser JSON válido (lista ou objeto)")
    out = {}
    if isinstance(rot_raw, dict):
        for k, v in rot_raw.items():
            try:
                page_1b = int(k)
                deg = int(v) % 360
                if deg < 0:
                    deg += 360
                if deg not in (0, 90, 180, 270):
                    deg = (round(deg / 90) * 90) % 360
                if deg != 0:
                    out[page_1b] = deg
            except Exception:
                continue
    elif isinstance(rot_raw, list):
        for idx0, v in enumerate(rot_raw):
            try:
                deg = int(v) % 360
                if deg < 0:
                    deg += 360
                if deg not in (0, 90, 180, 270):
                    deg = (round(deg / 90) * 90) % 360
                page_1b = idx0 + 1
                if deg != 0:
                    out[page_1b] = deg
            except Exception:
                continue
    else:
        raise ValueError("rotations/rot deve ser lista ou objeto JSON")
    return out or None


def _json_error(message: str, status: int = 400):
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


def _normalize_compression_mode(raw_mode) -> str:
    if raw_mode is None:
        return "manual"
    if not isinstance(raw_mode, str):
        raise ValueError("mode deve ser 'manual' ou 'target_size'.")
    mode = raw_mode.strip().lower()
    if mode not in _COMPRESSION_MODES:
        raise ValueError("mode deve ser 'manual' ou 'target_size'.")
    return mode


def _normalize_target_size(raw_target) -> tuple[Decimal, int]:
    if isinstance(raw_target, bool) or not isinstance(raw_target, (int, float)):
        raise ValueError("target_size_mb deve ser um número entre 0,20 e 50.")
    try:
        target_mb = Decimal(str(raw_target))
    except (InvalidOperation, ValueError):
        raise ValueError(
            "target_size_mb deve ser um número entre 0,20 e 50."
        ) from None
    if (
        not target_mb.is_finite()
        or target_mb < _MIN_TARGET_SIZE_MB
        or target_mb > _MAX_TARGET_SIZE_MB
    ):
        raise ValueError("target_size_mb deve ser um número entre 0,20 e 50.")

    effective_bytes = int(
        (
            target_mb
            * Decimal(1_000_000)
            * _TARGET_SIZE_MARGIN
        ).to_integral_value(rounding=ROUND_FLOOR)
    )
    return target_mb, effective_bytes


def _normalize_modern_page_settings(page_settings: list) -> tuple[dict, list, bool]:
    settings_by_page: dict[int, dict] = {}
    page_order: list[int] = []
    resize_requested = False

    for raw in page_settings:
        if not isinstance(raw, dict):
            continue
        try:
            page_number = int(raw.get("page_number", 0))
            if page_number < 1:
                continue
            if page_number not in settings_by_page:
                page_order.append(page_number)
            keep_original = bool(raw.get("keep_original", False))
            requested_resize = (
                bool(raw.get("resize_to_a4", False))
                and not keep_original
            )
            resize_requested = resize_requested or requested_resize
            settings_by_page[page_number] = {
                "include": bool(raw.get("include", True)),
                "quality": max(20, min(100, int(raw.get("quality", 80)))),
                "dpi": max(MIN_SAFE_DPI, min(300, int(raw.get("dpi", 100)))),
                "resize_to_a4": requested_resize,
                "keep_original": keep_original,
            }
        except (TypeError, ValueError):
            continue

    return settings_by_page, page_order, resize_requested


def _merge_selected_page_sources(
    page_sources: dict,
    included_pages: list[int],
    output_path: str,
) -> None:
    """Remonta as páginas na ordem solicitada, preservando streams quando possível."""
    try:
        import pikepdf  # noqa: PLC0415

        opened: dict = {}
        try:
            output_pdf = pikepdf.Pdf.new()
            for page_number in included_pages:
                source_path, source_index = page_sources[page_number]
                if source_path not in opened:
                    opened[source_path] = pikepdf.open(source_path)
                source_pdf = opened[source_path]
                if source_index >= len(source_pdf.pages):
                    raise RuntimeError("page_source_index_invalid")
                output_pdf.pages.append(source_pdf.pages[source_index])
            output_pdf.save(output_path)
        finally:
            for pdf_obj in opened.values():
                try:
                    pdf_obj.close()
                except Exception:
                    pass
    except Exception as pike_error:
        current_app.logger.warning(
            "[process-with-settings] montagem pikepdf falhou: %s",
            type(pike_error).__name__,
        )
        writer = PdfWriter()
        for page_number in included_pages:
            source_path, source_index = page_sources[page_number]
            with open(source_path, "rb") as handle:
                reader = PdfReader(handle)
                if source_index >= len(reader.pages):
                    raise RuntimeError("page_source_index_invalid")
                writer.add_page(reader.pages[source_index])
        with open(output_path, "wb") as output_handle:
            writer.write(output_handle)


def _log_preservation_facts(
    preservation: dict,
    included_pages: list[int],
) -> None:
    included_set = set(included_pages)
    interactive_pages = [
        page_number
        for page_number in preservation.get("interactive_pages", [])
        if page_number in included_set
    ]
    compressible_pages = [
        page_number
        for page_number in preservation.get("compressible_pages", [])
        if page_number in included_set
    ]
    current_app.logger.info(
        "[compress-preservation] pages=%d acroform=%s fields=%d "
        "filled_fields=%d widgets=%d signature_fields=%d "
        "real_signatures=%d annotations=%d appearances=%d "
        "need_appearances=%s sigflags=%s full_preservation=%s "
        "interactive_pages=%d compressible_pages=%d",
        len(included_pages),
        preservation.get("has_acroform", False),
        preservation.get("field_count", 0),
        preservation.get("filled_field_count", 0),
        preservation.get("widget_count", 0),
        preservation.get("signature_field_count", 0),
        preservation.get("real_signature_count", 0),
        preservation.get("annotation_count", 0),
        preservation.get("annotation_appearance_count", 0),
        preservation.get("has_need_appearances", False),
        preservation.get("has_sigflags", False),
        preservation.get("requires_full_document_preservation", False),
        len(interactive_pages),
        len(compressible_pages),
    )
    for page in preservation.get("pages", []):
        page_number = page.get("page_number")
        if page_number not in included_set:
            continue
        current_app.logger.debug(
            "[compress-preservation-page] page=%d interactive=%s "
            "annotations=%d fields=%d filled_fields=%d widgets=%d "
            "other_annotations=%d "
            "appearances=%d signature_fields=%d real_signatures=%d",
            page_number,
            page.get("interactive", False),
            page.get("annotation_count", 0),
            page.get("field_count", 0),
            page.get("filled_field_count", 0),
            page.get("widget_count", 0),
            page.get("non_widget_annotation_count", 0),
            page.get("annotation_appearance_count", 0),
            page.get("signature_field_count", 0),
            page.get("real_signature_count", 0),
        )


def _select_initial_target_profile_index(
    target_size_bytes: int,
    baseline_size: int,
) -> int:
    ratio = target_size_bytes / baseline_size if baseline_size else 1.0
    if ratio >= 0.85:
        return 0
    if ratio >= 0.70:
        return 1
    if ratio >= 0.58:
        return 2
    if ratio >= 0.45:
        return 3
    if ratio >= 0.32:
        return 4
    if ratio >= 0.20:
        return 5
    return len(TARGET_SIZE_PROFILES) - 1


def _pdf_visual_page_layout(path: str) -> list[tuple[float, float]]:
    with open(path, "rb") as handle:
        reader = PdfReader(handle)
        layout = []
        for page in reader.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            rotation = int(page.get("/Rotate", 0) or 0) % 360
            if rotation in (90, 270):
                width, height = height, width
            layout.append((round(width, 1), round(height, 1)))
        return layout


def _validate_target_candidate(
    baseline_path: str,
    candidate_path: str,
) -> list[str]:
    warnings = validate_compressed_pdf(baseline_path, candidate_path)
    if warnings:
        return warnings
    try:
        baseline_layout = _pdf_visual_page_layout(baseline_path)
        candidate_layout = _pdf_visual_page_layout(candidate_path)
    except Exception:
        return ["candidate_layout_unreadable"]
    if len(baseline_layout) != len(candidate_layout):
        return ["page_count_mismatch"]
    for expected, actual in zip(baseline_layout, candidate_layout):
        if (
            abs(expected[0] - actual[0]) > 1.0
            or abs(expected[1] - actual[1]) > 1.0
        ):
            return ["page_order_or_rotation_mismatch"]
    return []


def _run_target_profile_attempt(
    *,
    baseline_path: str,
    candidate_path: str,
    profile: dict,
    compress_positions: list[int],
    keep_positions: list[int],
    upload_folder: str,
    temporary_files: list[str],
    timeout_seconds: int,
    preserve_catalog: bool = False,
):
    compression_kwargs = {
        "input_path": baseline_path,
        "pages": compress_positions,
        "quality": profile["quality"],
        "dpi": profile["dpi"],
        "resize_to_a4": False,
        "rotations": None,
    }
    if profile.get("force_jpeg_recompression"):
        compression_kwargs["force_jpeg_recompression"] = True
    if profile.get("convert_to_grayscale"):
        compression_kwargs["convert_to_grayscale"] = True
    compression_kwargs["timeout_seconds"] = timeout_seconds

    if not keep_positions and not preserve_catalog:
        return comprimir_pdf_com_params(
            output_path=candidate_path,
            **compression_kwargs,
        )

    group_output = os.path.join(
        upload_folder,
        f"target_group_{uuid.uuid4().hex}.pdf",
    )
    temporary_files.append(group_output)
    try:
        group_warnings = comprimir_pdf_com_params(
            output_path=group_output,
            **compression_kwargs,
        )
        compressed_indexes = {
            position: index
            for index, position in enumerate(compress_positions)
        }
        replacements = {
            position: (group_output, source_index)
            for position, source_index in compressed_indexes.items()
        }
        replace_pdf_pages_preserving_catalog(
            baseline_path,
            candidate_path,
            replacements,
        )
        return group_warnings
    finally:
        cleanup_upload_files((group_output,), upload_folder)


def _search_target_size(
    *,
    analyse_id: str,
    upload_folder: str,
    lock_token: str,
    baseline_path: str,
    baseline_size: int,
    target_size_bytes: int,
    compress_positions: list[int],
    keep_positions: list[int],
    temporary_files: list[str],
    allow_grayscale: bool,
    total_timeout_sec: int,
    started_at: float,
    preserve_catalog: bool = False,
) -> dict:
    deadline = started_at + total_timeout_sec
    attempts = 0
    candidates: list[dict] = []
    warnings: list[str] = []
    budget_exhausted = False

    def run_attempt(profile: dict, rank: int) -> dict | None:
        nonlocal attempts, budget_exhausted
        if budget_exhausted or attempts >= MAX_TARGET_COMPRESSION_ATTEMPTS:
            return None
        if not _refresh_process_lock(analyse_id, upload_folder, lock_token):
            raise RuntimeError("process_lock_lost")
        remaining_seconds = deadline - _target_clock()
        if remaining_seconds < _MIN_TARGET_ATTEMPT_REMAINING_SEC:
            budget_exhausted = True
            if "target_timeout_budget_exhausted" not in warnings:
                warnings.append("target_timeout_budget_exhausted")
            return None
        per_attempt_budget = max(
            _MIN_TARGET_ATTEMPT_REMAINING_SEC,
            total_timeout_sec // MAX_TARGET_COMPRESSION_ATTEMPTS,
        )
        attempt_timeout = max(
            _MIN_TARGET_ATTEMPT_REMAINING_SEC,
            min(
                int(remaining_seconds),
                per_attempt_budget,
                max(
                    _MIN_TARGET_ATTEMPT_REMAINING_SEC,
                    GHOSTSCRIPT_TIMEOUT - 1,
                ),
            ),
        )

        candidate_path = os.path.join(
            upload_folder,
            f"target_candidate_{uuid.uuid4().hex}.pdf",
        )
        temporary_files.append(candidate_path)
        attempts += 1
        try:
            attempt_warnings = _run_target_profile_attempt(
                baseline_path=baseline_path,
                candidate_path=candidate_path,
                profile=profile,
                compress_positions=compress_positions,
                keep_positions=keep_positions,
                upload_folder=upload_folder,
                temporary_files=temporary_files,
                timeout_seconds=attempt_timeout,
                preserve_catalog=preserve_catalog,
            )
        except Exception:
            cleanup_upload_files((candidate_path,), upload_folder)
            raise

        if attempt_warnings:
            warnings.extend(attempt_warnings)
        fallback_reason = getattr(attempt_warnings, "fallback_reason", None)
        if fallback_reason:
            warnings.append(f"target_attempt_fallback:{fallback_reason}")

        validation_warnings = _validate_target_candidate(
            baseline_path,
            candidate_path,
        )
        candidate_size = (
            os.path.getsize(candidate_path)
            if os.path.exists(candidate_path)
            else 0
        )
        gain_ratio = (
            1 - candidate_size / baseline_size
            if baseline_size and candidate_size
            else 0.0
        )
        if (
            validation_warnings
            or not validate_pdf_readable(candidate_path)
            or candidate_size >= baseline_size
            or gain_ratio < MIN_COMPRESSION_GAIN_RATIO
        ):
            warnings.extend(validation_warnings)
            cleanup_upload_files((candidate_path,), upload_folder)
            return None

        record = {
            "path": candidate_path,
            "size": candidate_size,
            "profile": profile["slug"],
            "rank": rank,
            "achieved": candidate_size <= target_size_bytes,
        }
        candidates.append(record)
        return record

    def finalize() -> dict:
        elapsed_seconds = max(0.0, _target_clock() - started_at)
        exhausted = budget_exhausted or elapsed_seconds >= total_timeout_sec
        if exhausted and "target_timeout_budget_exhausted" not in warnings:
            warnings.append("target_timeout_budget_exhausted")
        achieved = [candidate for candidate in candidates if candidate["achieved"]]
        if achieved:
            selected = min(
                achieved,
                key=lambda candidate: (candidate["rank"], candidate["size"]),
            )
        elif candidates:
            selected = min(
                candidates,
                key=lambda candidate: (candidate["size"], candidate["rank"]),
            )
        else:
            selected = {
                "path": baseline_path,
                "size": baseline_size,
                "profile": "baseline",
                "rank": len(TARGET_SIZE_PROFILES) + 1,
                "achieved": False,
            }

        for candidate in candidates:
            if candidate["path"] != selected["path"]:
                cleanup_upload_files((candidate["path"],), upload_folder)
        return {
            **selected,
            "attempts": attempts,
            "warnings": warnings,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "budget_exhausted": exhausted,
        }

    initial_index = _select_initial_target_profile_index(
        target_size_bytes,
        baseline_size,
    )
    initial = run_attempt(TARGET_SIZE_PROFILES[initial_index], initial_index)
    if initial and initial["achieved"]:
        if initial_index > 0 and attempts < MAX_TARGET_COMPRESSION_ATTEMPTS:
            run_attempt(
                TARGET_SIZE_PROFILES[initial_index - 1],
                initial_index - 1,
            )
        return finalize()

    strongest_index = len(TARGET_SIZE_PROFILES) - 1
    strongest = initial if initial_index == strongest_index else run_attempt(
        TARGET_SIZE_PROFILES[strongest_index],
        strongest_index,
    )
    if strongest and strongest["achieved"]:
        if (
            initial_index < strongest_index - 1
            and attempts < MAX_TARGET_COMPRESSION_ATTEMPTS
        ):
            middle_index = (initial_index + strongest_index) // 2
            run_attempt(TARGET_SIZE_PROFILES[middle_index], middle_index)
        return finalize()

    if attempts < MAX_TARGET_COMPRESSION_ATTEMPTS:
        if allow_grayscale:
            run_attempt(
                TARGET_GRAYSCALE_PROFILE,
                len(TARGET_SIZE_PROFILES) + 1,
            )
        else:
            run_attempt(
                TARGET_JPEG_RECOMPRESSION_PROFILE,
                len(TARGET_SIZE_PROFILES),
            )
    return finalize()


def _build_process_response(
    output_path: str,
    *,
    uploaded_size: int,
    baseline_size: int,
    final_size: int,
    fallback_type: str,
    warnings: list[str],
    upload_folder: str,
    target_size_bytes: int | None = None,
    target_achieved: bool | None = None,
    compression_attempts: int | None = None,
    compression_profile: str | None = None,
    compression_elapsed_seconds: float | None = None,
):
    reduction_pct = (
        round((1 - final_size / baseline_size) * 100, 1)
        if baseline_size and final_size < baseline_size
        else 0.0
    )
    response = send_file(
        output_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="comprimido.pdf",
    )
    response.headers["X-Size-Uploaded-Bytes"] = str(uploaded_size)
    response.headers["X-Size-Baseline-Bytes"] = str(baseline_size)
    response.headers["X-Size-Final-Bytes"] = str(final_size)
    response.headers["X-Size-Original-KB"] = str(round(uploaded_size / 1024, 1))
    response.headers["X-Size-Baseline-KB"] = str(round(baseline_size / 1024, 1))
    response.headers["X-Size-Final-KB"] = str(round(final_size / 1024, 1))
    response.headers["X-Reduction-Pct"] = str(reduction_pct)
    response.headers["X-Baseline-Reduction-Pct"] = str(reduction_pct)
    response.headers["X-Fallback"] = fallback_type

    unique_warnings = list(dict.fromkeys(warnings))
    if unique_warnings or target_size_bytes is not None:
        response.headers["X-Compress-Warnings"] = "; ".join(unique_warnings)
    exposed = [
        "X-Size-Uploaded-Bytes",
        "X-Size-Baseline-Bytes",
        "X-Size-Final-Bytes",
        "X-Size-Original-KB",
        "X-Size-Baseline-KB",
        "X-Size-Final-KB",
        "X-Reduction-Pct",
        "X-Baseline-Reduction-Pct",
        "X-Fallback",
    ]
    if unique_warnings or target_size_bytes is not None:
        exposed.append("X-Compress-Warnings")
    if target_size_bytes is not None:
        response.headers["X-Target-Size-Bytes"] = str(target_size_bytes)
        response.headers["X-Target-Achieved"] = (
            "true" if target_achieved else "false"
        )
        response.headers["X-Compression-Attempts"] = str(
            max(0, int(compression_attempts or 0))
        )
        response.headers["X-Compression-Profile"] = (
            compression_profile or "baseline"
        )
        response.headers["X-Compression-Elapsed-Sec"] = str(
            round(max(0.0, float(compression_elapsed_seconds or 0.0)), 3)
        )
        exposed.extend(
            [
                "X-Target-Size-Bytes",
                "X-Target-Achieved",
                "X-Compression-Attempts",
                "X-Compression-Profile",
                "X-Compression-Elapsed-Sec",
            ]
        )
    response.headers["Access-Control-Expose-Headers"] = ", ".join(exposed)
    register_response_file_cleanup(response, (output_path,), upload_folder)
    return response


def _extract_pdf_metadata(file_path: str) -> dict:
    try:
        with open(file_path, "rb") as f:
            reader = PdfReader(f)
            total_pages = len(reader.pages)
            total_size_bytes = os.path.getsize(file_path)
            page_areas = []
            total_area = 0
            for idx in range(total_pages):
                mb = reader.pages[idx].mediabox
                w, h = float(mb.width), float(mb.height)
                area = w * h
                page_areas.append((w, h, area))
                total_area += area
            avg_area = total_area / total_pages if total_pages > 0 else 1
            pages = []
            for idx, (w, h, area) in enumerate(page_areas):
                estimated_kb = (area / total_area) * (total_size_bytes / 1024) if total_area > 0 else 0
                pages.append({
                    "page_number":       idx + 1,
                    "width":             round(w, 1),
                    "height":            round(h, 1),
                    "area":              area,
                    "estimated_size_kb": round(estimated_kb, 1),
                    "is_large":          area > (avg_area * 1.3),
                })
            return {
                "total_pages":      total_pages,
                "total_size_bytes": total_size_bytes,
                "total_size_mb":    str(round(total_size_bytes / (1024 * 1024), 1)),
                "pages":            pages,
            }
    except Exception as e:
        current_app.logger.error("Erro ao extrair metadados PDF: %s", type(e).__name__)
        raise


def _generate_page_thumbnail(pdf_path: str, page_index: int) -> str:
    """
    Gera JPEG de thumbnail a 144 DPI (240×338 px) para nitidez em DPR até 2×.
    Única definição — sem versão antiga com parâmetro quality.
    """
    pdf_path = os.path.abspath(pdf_path)
    page_num = page_index + 1
    temp_png = None
    try:
        gs_cmd = _get_ghostscript_cmd() or "gs"
        temp_fd, temp_png = tempfile.mkstemp(
            suffix=".png",
            prefix="gs_thumb_",
            dir=os.path.dirname(os.path.abspath(pdf_path)),
        )
        os.close(temp_fd)
        cmd = [
            gs_cmd, "-dNOPAUSE", "-dBATCH", "-dSAFER",
            "-sDEVICE=png16m", "-r144",
            f"-dFirstPage={page_num}", f"-dLastPage={page_num}",
            f"-sOutputFile={temp_png}", pdf_path,
        ]
        result = run_ghostscript_command(
            cmd,
            working_parent=os.path.dirname(os.path.abspath(pdf_path)),
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError("ghostscript_thumbnail_failed")
        if not os.path.exists(temp_png):
            raise RuntimeError(f"Ghostscript não gerou PNG para página {page_num}")
        with Image.open(temp_png) as img:
            img.thumbnail((240, 338), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=88)
            jpeg_bytes = buf.getvalue()
        return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
    except Exception as e:
        current_app.logger.warning("Thumbnail página %d falhou: %s — usando placeholder", page_num, type(e).__name__)
        svg = (
            f'<svg width="200" height="280" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="200" height="280" fill="#eee"/>'
            f'<text x="100" y="140" font-family="Arial" font-size="14" '
            f'fill="#999" text-anchor="middle" dy=".3em">Página {page_num}</text></svg>'
        )
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode("ascii")
    finally:
        if temp_png and os.path.exists(temp_png):
            try:
                os.remove(temp_png)
            except OSError:
                pass


# ── endpoints ─────────────────────────────────────────────────────────────────

@compress_bp.route("", methods=["POST"])
@compress_bp.route("/", methods=["POST"])
@limiter.limit("5 per minute")
def compress():
    f = request.files.get("file")
    if not f or not f.filename:
        return _json_error("Nenhum arquivo enviado.", 400)
    profile = _normalize_profile(request.form.get("profile", "equilibrio"))
    modificacoes = None
    mods = request.form.get("modificacoes")
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return _json_error("modificacoes deve ser JSON válido", 400)
    try:
        pages = _normalize_pages(
            request.form.get("pages") or request.form.get("order") or request.form.get("page_order")
        )
    except ValueError as e:
        return _json_error(str(e), 400)
    raw_rot = (
        request.form.get("rotations") or request.form.get("rot")
        or request.headers.get("X-Rotations")
    )
    try:
        rotations = _normalize_rotations(raw_rot)
    except ValueError as e:
        return _json_error(str(e), 400)

    try:
        out_path, compress_warnings = comprimir_pdf(
            f, pages=pages, rotations=rotations,
            modificacoes=modificacoes, profile=profile,
        )

        response = send_file(out_path, mimetype="application/pdf",
                             as_attachment=False, download_name=os.path.basename(out_path))
        fallback_reason = getattr(compress_warnings, 'fallback_reason', None)
        response.headers['X-Fallback'] = fallback_reason or 'none'
        exposed_headers = ['X-Fallback']
        if compress_warnings:
            response.headers["X-Compress-Warnings"] = "; ".join(compress_warnings)
            exposed_headers.append('X-Compress-Warnings')
        response.headers['Access-Control-Expose-Headers'] = ', '.join(exposed_headers)
        register_response_file_cleanup(
            response, (out_path,), current_app.config["UPLOAD_FOLDER"]
        )
        return response
    except Exception as exc:
        current_app.logger.error("[compress] falha controlada: %s", type(exc).__name__)
        return _json_error("Falha ao comprimir o PDF.", 500)


@compress_bp.get("/profiles")
def list_profiles():
    return jsonify({k: {"label": v["label"], "hint": v["hint"]} for k, v in USER_PROFILES.items()})


@compress_bp.post("/analyze")
@limiter.limit("10 per minute")
def analyze():
    f = request.files.get("file")
    if not f or not f.filename:
        return _json_error("Nenhum arquivo PDF enviado.", 400)
    if not f.filename.lower().endswith(".pdf"):
        return _json_error("O arquivo deve ser um PDF (.pdf).", 400)
    f.seek(0)
    header = f.read(4)
    f.seek(0)
    if header != b"%PDF":
        return _json_error("O arquivo não é um PDF válido.", 400)

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)
    try:
        clean_filename = validate_upload(f, {"pdf"})
    except Exception as e:
        current_app.logger.warning("Validação de upload falhou: %s", type(e).__name__)
        return _json_error("Arquivo não passou na validação.", 400)

    temp_id       = uuid.uuid4().hex
    temp_path     = os.path.join(upload_folder, f"analyze_{temp_id}_{clean_filename}")
    analysis_path = None
    uploaded_size_bytes = 0

    try:
        f.save(temp_path)
        uploaded_size_bytes = os.path.getsize(temp_path)
        sanitized_path = os.path.join(upload_folder, f"sanitized_{temp_id}_{clean_filename}")
        try:
            sanitize_pdf_preserving_content(temp_path, sanitized_path)
            analysis_path = sanitized_path
            try:
                os.remove(temp_path)
            except OSError:
                pass
        except Exception as e:
            current_app.logger.warning("[analyze] sanitizacao falhou: %s", type(e).__name__)
            raise RuntimeError("sanitize_failed") from e

        try:
            validated_pages = enforce_pdf_page_limit(
                analysis_path,
                label="PDF",
            )
        except BadRequest as exc:
            cleanup_upload_files((temp_path, analysis_path), upload_folder)
            description = str(getattr(exc, "description", "") or "")
            if "acima do limite" in description:
                return _json_error(
                    "O PDF excede o limite de "
                    f"{get_max_pdf_pages()} paginas permitido para analise.",
                    422,
                )
            return _json_error(
                "O PDF esta vazio, ilegivel ou nao possui paginas validas.",
                422,
            )

        metadata  = _extract_pdf_metadata(analysis_path)
        if metadata["total_pages"] != validated_pages:
            raise RuntimeError("page_count_changed_during_analysis")
        has_large = False
        pages_data = []

        for page_meta in metadata["pages"]:
            if page_meta["is_large"]:
                has_large = True
            thumb = _generate_page_thumbnail(analysis_path, page_meta["page_number"] - 1)
            # Defaults neutros — serão sobrescritos por enrich_page_analysis abaixo
            pages_data.append({
                "page_number":       page_meta["page_number"],
                "width":             page_meta["width"],
                "height":            page_meta["height"],
                "estimated_size_kb": page_meta["estimated_size_kb"],
                "is_large":          page_meta["is_large"],
                "area":              page_meta["area"],
                "thumbnail":         thumb,
                "quality":           80,
                "dpi":               100,
                "include":           True,
                "resize_to_a4":      False,
                "keep_original":     False,
            })

        # enrich_page_analysis é a única fonte de verdade para quality/dpi/resize sugeridos.
        # Calcula size_factor, is_large refinado, quality_suggested, dpi_suggested por página.
        pages_data = enrich_page_analysis(pages_data)

        analyse_id = uuid.uuid4().hex
        owner_id = _get_or_create_compress_owner_id()
        _purge_expired_sessions()
        _session_set(
            analyse_id,
            analysis_path,
            upload_folder,
            owner_id,
            uploaded_size_bytes=uploaded_size_bytes,
        )

        return jsonify({
            "analyse_id":      analyse_id,
            "filename":        clean_filename,
            "total_pages":     metadata["total_pages"],
            "total_size_mb":   metadata["total_size_mb"],
            "uploaded_size_bytes": uploaded_size_bytes,
            "has_large_pages": has_large,
            "pages":           pages_data,
        }), 200

    except Exception as e:
        current_app.logger.error("[analyze] falha controlada: %s", type(e).__name__)
        for p in [temp_path, analysis_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return _json_error("Falha ao analisar o PDF. Tente novamente.", 500)


@compress_bp.post("/process-with-settings")
@limiter.limit(_process_with_settings_rate_limit)
def process_with_settings():
    data = request.get_json(silent=True)
    if not data:
        return _json_error("Payload JSON inválido ou ausente.", 400)

    raw_analyse_id = data.get("analyse_id", "")
    analyse_id = raw_analyse_id.strip() if isinstance(raw_analyse_id, str) else ""
    page_settings = data.get("page_settings")
    rotations_raw = data.get("rotations")
    try:
        mode = _normalize_compression_mode(data.get("mode"))
        if mode == "target_size":
            _target_size_mb, target_size_bytes = _normalize_target_size(
                data.get("target_size_mb")
            )
            allow_grayscale = data.get("allow_grayscale", False)
            if not isinstance(allow_grayscale, bool):
                raise ValueError("allow_grayscale deve ser booleano.")
        else:
            target_size_bytes = None
            allow_grayscale = False
    except ValueError as exc:
        return _json_error(str(exc), 400)

    if not analyse_id:
        return _json_error("analyse_id é obrigatório.", 400)
    if not page_settings or not isinstance(page_settings, list):
        return _json_error("page_settings deve ser uma lista.", 400)

    session_details = _session_get_details(analyse_id)
    if not session_details:
        return _json_error("Sessão expirada ou não encontrada. Faça upload novamente.", 404)
    source_path = session_details["path"]
    uploaded_size = session_details["uploaded_size_bytes"]

    settings_by_page, page_order, resize_requested = (
        _normalize_modern_page_settings(page_settings)
    )
    included_pages = [
        page_number
        for page_number in page_order
        if settings_by_page[page_number]["include"]
    ]
    if not included_pages:
        return _json_error("Nenhuma página selecionada para incluir.", 400)

    try:
        rotations = _normalize_rotations(rotations_raw) if rotations_raw else None
    except ValueError as e:
        return _json_error(str(e), 400)

    source_page_count = count_pdf_pages(source_path)
    if source_page_count <= 0:
        return _json_error("PDF de origem inválido ou sem páginas.", 422)
    if any(page_number > source_page_count for page_number in included_pages):
        return _json_error("A seleção contém uma página inválida.", 400)

    pages_keep = [
        page_number
        for page_number in included_pages
        if settings_by_page[page_number]["keep_original"]
    ]
    pages_resize = [
        page_number
        for page_number in included_pages
        if settings_by_page[page_number]["resize_to_a4"]
    ]
    compress_groups: dict = {}
    for page_number in included_pages:
        page_config = settings_by_page[page_number]
        if page_config["keep_original"]:
            continue
        key = (
            page_config["quality"],
            page_config["dpi"],
            page_config["resize_to_a4"],
        )
        compress_groups.setdefault(key, []).append(page_number)

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    ensure_upload_folder_exists(upload_folder)
    lock_token = _acquire_process_lock(analyse_id, upload_folder)
    if not lock_token:
        return _json_error(
            "Esta análise já está sendo processada. Aguarde a conclusão.",
            409,
        )

    out_path = None
    response_path = None
    baseline_path = os.path.join(
        upload_folder,
        f"selected_baseline_{uuid.uuid4().hex}.pdf",
    )
    temporary_files = [baseline_path]
    group_files = []
    all_compress_warnings: list = []
    target_total_timeout_sec = (
        _target_compression_total_timeout_sec()
        if mode == "target_size"
        else None
    )
    target_started_at = _target_clock() if mode == "target_size" else None

    try:
        # Revalida após adquirir o lock para fechar a corrida com outro worker.
        session_details = _session_get_details(analyse_id)
        if not session_details:
            return _json_error(
                "Sessão expirada ou não encontrada. Faça upload novamente.",
                404,
            )
        source_path = session_details["path"]
        uploaded_size = session_details["uploaded_size_bytes"]
        if not _refresh_process_lock(analyse_id, upload_folder, lock_token):
            raise RuntimeError("process_lock_lost")

        preservation = pdf_requires_content_preservation(source_path)
        _log_preservation_facts(preservation, included_pages)
        interactive_page_set = set(
            preservation.get("interactive_pages", [])
        )
        selected_interactive_pages = [
            page_number
            for page_number in included_pages
            if page_number in interactive_page_set
        ]
        selected_compressible_pages = [
            page_number
            for page_number in included_pages
            if page_number not in interactive_page_set
        ]
        full_document_preservation = bool(
            preservation.get("requires_full_document_preservation")
        )
        build_selected_baseline(
            source_path,
            baseline_path,
            included_pages,
            rotations=rotations,
            resize_pages=(
                pages_resize
                if not preservation.get("requires_preservation")
                else None
            ),
            preserve_interactive=bool(preservation.get("requires_preservation")),
        )
        baseline_size = os.path.getsize(baseline_path)

        if mode == "target_size":
            if preservation.get("requires_preservation"):
                all_compress_warnings.extend(
                    pdf_preservation_warnings(
                        preservation,
                        resize_to_a4=resize_requested,
                        selective=bool(
                            not full_document_preservation
                            and selected_interactive_pages
                            and selected_compressible_pages
                        ),
                    )
                )

            compress_positions: list[int] = []
            keep_positions: list[int] = []
            target_result = {
                "path": baseline_path,
                "size": baseline_size,
                "profile": "baseline",
                "attempts": 0,
                "achieved": baseline_size <= target_size_bytes,
                "warnings": [],
                "elapsed_seconds": 0.0,
                "budget_exhausted": False,
            }
            if not target_result["achieved"]:
                if full_document_preservation:
                    target_result["warnings"].append(
                        "target_not_achieved:cryptographic_or_unscoped_content_preserved"
                    )
                else:
                    keep_page_set = set(pages_keep)
                    compress_positions = [
                        index
                        for index, page_number in enumerate(
                            included_pages,
                            start=1,
                        )
                        if (
                            page_number not in keep_page_set
                            and page_number not in interactive_page_set
                        )
                    ]
                    keep_positions = [
                        index
                        for index, page_number in enumerate(
                            included_pages,
                            start=1,
                        )
                        if (
                            page_number in keep_page_set
                            or page_number in interactive_page_set
                        )
                    ]
                    if compress_positions:
                        target_result = _search_target_size(
                            analyse_id=analyse_id,
                            upload_folder=upload_folder,
                            lock_token=lock_token,
                            baseline_path=baseline_path,
                            baseline_size=baseline_size,
                            target_size_bytes=target_size_bytes,
                            compress_positions=compress_positions,
                            keep_positions=keep_positions,
                            temporary_files=temporary_files,
                            allow_grayscale=allow_grayscale,
                            total_timeout_sec=target_total_timeout_sec,
                            started_at=target_started_at,
                            preserve_catalog=bool(
                                preservation.get("requires_preservation")
                            ),
                        )
                    else:
                        target_result["warnings"].append(
                            "target_not_achieved:interactive_content_preserved"
                            if selected_interactive_pages
                            else "target_not_achieved:all_pages_keep_original"
                        )

            if target_result["attempts"] == 0:
                target_result["elapsed_seconds"] = round(
                    max(0.0, _target_clock() - target_started_at),
                    3,
                )
                if (
                    target_result["elapsed_seconds"]
                    >= target_total_timeout_sec
                ):
                    target_result["budget_exhausted"] = True
                    target_result["warnings"].append(
                        "target_timeout_budget_exhausted"
                    )

            all_compress_warnings.extend(target_result["warnings"])
            if target_result["profile"] == "recompressao_jpeg_agressiva":
                all_compress_warnings.append(
                    "recompressao_jpeg_agressiva"
                )
            if target_result["profile"] == "tons_de_cinza":
                all_compress_warnings.append("tons_de_cinza_aplicados")
            if not target_result["achieved"]:
                all_compress_warnings.append("target_not_achieved")

            out_path = target_result["path"]
            final_size = target_result["size"]
            if out_path == baseline_path:
                fallback_type = (
                    "preserved_interactive"
                    if (
                        full_document_preservation
                        or (
                            preservation.get("requires_preservation")
                            and not compress_positions
                        )
                    )
                    else "selected_baseline"
                )
            elif selected_interactive_pages:
                fallback_type = "partial_interactive_preservation"
            elif target_result["achieved"]:
                fallback_type = "final_compressed"
            else:
                fallback_type = "target_not_achieved"

            current_app.logger.info(
                "[process-with-settings] mode=target_size result=%s "
                "profile=%s attempts=%d achieved=%s pages=%d reduction=%.1f%%",
                fallback_type,
                target_result["profile"],
                target_result["attempts"],
                target_result["achieved"],
                len(included_pages),
                (
                    (1 - final_size / baseline_size) * 100
                    if baseline_size
                    else 0.0
                ),
            )

            response = _build_process_response(
                out_path,
                uploaded_size=uploaded_size,
                baseline_size=baseline_size,
                final_size=final_size,
                fallback_type=fallback_type,
                warnings=all_compress_warnings,
                upload_folder=upload_folder,
                target_size_bytes=target_size_bytes,
                target_achieved=target_result["achieved"],
                compression_attempts=target_result["attempts"],
                compression_profile=target_result["profile"],
                compression_elapsed_seconds=target_result["elapsed_seconds"],
            )
            response_path = out_path
            _session_delete(analyse_id, upload_folder)
            cleanup_upload_files((source_path,), upload_folder)
            return response

        if full_document_preservation:
            out_path = baseline_path
            all_compress_warnings.extend(
                pdf_preservation_warnings(preservation, resize_to_a4=resize_requested)
            )
            final_size = os.path.getsize(out_path)
            current_app.logger.info(
                "[process-with-settings] modo_preservador pages=%d warnings=%d",
                len(included_pages), len(all_compress_warnings),
            )

            response = _build_process_response(
                out_path,
                uploaded_size=uploaded_size,
                baseline_size=baseline_size,
                final_size=final_size,
                fallback_type="preserved_interactive",
                warnings=all_compress_warnings,
                upload_folder=upload_folder,
            )
            response_path = out_path
            _session_delete(analyse_id, upload_folder)
            cleanup_upload_files((source_path,), upload_folder)
            return response

        if preservation.get("requires_preservation"):
            all_compress_warnings.extend(
                pdf_preservation_warnings(
                    preservation,
                    resize_to_a4=resize_requested,
                    selective=bool(
                        selected_interactive_pages
                        and selected_compressible_pages
                    ),
                )
            )
            pages_keep = list(
                dict.fromkeys(pages_keep + selected_interactive_pages)
            )
            compress_groups = {
                key: [
                    page_number
                    for page_number in group_pages
                    if page_number not in interactive_page_set
                ]
                for key, group_pages in compress_groups.items()
            }
            compress_groups = {
                key: group_pages
                for key, group_pages in compress_groups.items()
                if group_pages
            }

        # page_sources[pn] = (arquivo_origem, idx_0based)
        # Preserva referência ao grupo comprimido real em vez de reescrever com PdfWriter.
        page_sources: dict = {}
        group_fallback_count = 0

        for (quality, dpi, resize_to_a4), group_pages in compress_groups.items():
            if not _refresh_process_lock(analyse_id, upload_folder, lock_token):
                raise RuntimeError("process_lock_lost")
            group_rotations = None
            if rotations:
                group_rotations = {pn: deg for pn, deg in rotations.items()
                                   if pn in group_pages} or None
            group_out = os.path.join(upload_folder, f"group_{uuid.uuid4().hex}.pdf")
            group_files.append(group_out)
            temporary_files.append(group_out)
            group_warnings = comprimir_pdf_com_params(
                input_path=source_path, output_path=group_out,
                pages=group_pages, quality=quality, dpi=dpi,
                resize_to_a4=resize_to_a4, rotations=group_rotations,
            )
            if getattr(group_warnings, "used_original", False):
                group_fallback_count += 1
            if group_warnings:
                all_compress_warnings.extend(group_warnings)
            for idx, pn in enumerate(group_pages):
                page_sources[pn] = (group_out, idx)

        if (
            not preservation.get("requires_preservation")
            and not pages_keep
            and len(compress_groups) == 1
        ):
            out_path = group_files[0]
        elif not compress_groups:
            out_path = baseline_path
        else:
            # Páginas keep_original já estão corretas no baseline selecionado.
            if pages_keep:
                baseline_positions = {
                    page_number: index
                    for index, page_number in enumerate(included_pages)
                }
                for page_number in pages_keep:
                    page_sources[page_number] = (
                        baseline_path,
                        baseline_positions[page_number],
                    )

            out_path = os.path.join(upload_folder, f"merged_{uuid.uuid4().hex}.pdf")
            temporary_files.append(out_path)
            if not _refresh_process_lock(analyse_id, upload_folder, lock_token):
                raise RuntimeError("process_lock_lost")
            if preservation.get("requires_preservation"):
                baseline_positions = {
                    page_number: index
                    for index, page_number in enumerate(
                        included_pages,
                        start=1,
                    )
                }
                replacements = {
                    baseline_positions[page_number]: source
                    for page_number, source in page_sources.items()
                    if source[0] != baseline_path
                }
                replace_pdf_pages_preserving_catalog(
                    baseline_path,
                    out_path,
                    replacements,
                )
            else:
                _merge_selected_page_sources(
                    page_sources,
                    included_pages,
                    out_path,
                )

        final_warnings = validate_compressed_pdf(baseline_path, out_path)
        final_size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        gain_ratio = (
            1 - final_size / baseline_size
            if baseline_size and final_size
            else 0.0
        )
        use_selected_baseline = (
            out_path == baseline_path
            or bool(final_warnings)
            or not validate_pdf_readable(out_path)
            or final_size >= baseline_size
            or gain_ratio < MIN_COMPRESSION_GAIN_RATIO
        )

        if use_selected_baseline:
            if final_warnings:
                all_compress_warnings.extend(final_warnings)
            out_path = baseline_path
            final_size = baseline_size
            fallback_type = (
                "preserved_interactive"
                if (
                    preservation.get("requires_preservation")
                    and not compress_groups
                )
                else "selected_baseline"
            )
        elif selected_interactive_pages:
            fallback_type = "partial_interactive_preservation"
        elif group_fallback_count == len(compress_groups):
            fallback_type = "group_original"
        elif group_fallback_count:
            fallback_type = "partial"
        else:
            fallback_type = "final_compressed"

        current_app.logger.info(
            "[process-with-settings] result=%s pages=%d reduction=%.1f%%",
            fallback_type,
            len(included_pages),
            (1 - final_size / baseline_size) * 100 if baseline_size else 0.0,
        )

        response = _build_process_response(
            out_path,
            uploaded_size=uploaded_size,
            baseline_size=baseline_size,
            final_size=final_size,
            fallback_type=fallback_type,
            warnings=all_compress_warnings,
            upload_folder=upload_folder,
        )
        response_path = out_path
        _session_delete(analyse_id, upload_folder)
        cleanup_upload_files((source_path,), upload_folder)
        return response

    except Exception as exc:
        current_app.logger.error(
            "[process-with-settings] falha controlada: %s", type(exc).__name__
        )
        # Erros recuperáveis preservam a análise para uma nova tentativa.
        return _json_error("Falha ao processar o PDF. Tente novamente.", 500)

    finally:
        cleanup_upload_files(
            (
                path
                for path in temporary_files
                if path and path != response_path
            ),
            upload_folder,
        )
        _release_process_lock(analyse_id, upload_folder, lock_token)
