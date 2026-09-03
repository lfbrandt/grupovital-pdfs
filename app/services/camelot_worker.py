# -*- coding: utf-8 -*-
"""Worker interno e sem estado para extrações Camelot do PDF -> XLSX."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


PROTOCOL_VERSION = 1

EXIT_OK = 0
EXIT_REQUEST_INVALID = 2
EXIT_EXTRACTION_FAILED = 3
EXIT_RESULT_FAILED = 4

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024
MAX_TABLES = 800
MAX_TOTAL_ROWS = 100_000
MAX_COLUMNS = 256
MAX_TOTAL_CELLS = 2_000_000
MAX_CELL_CHARS = 65_536
MAX_REQUESTED_PAGES = 800
MAX_PAGE_NUMBER = 10_000
MAX_PAGES_SPEC_CHARS = 8_192

ALLOWED_EXTRACTORS = frozenset({
    "camelot-lattice-smart",
    "camelot-stream-smart",
    "camelot-lattice-global",
    "camelot-lattice-region",
    "camelot-stream-global",
})
ALLOWED_EXTRACTORS_BY_FLAVOR = {
    "lattice": frozenset({
        "camelot-lattice-smart",
        "camelot-lattice-global",
        "camelot-lattice-region",
    }),
    "stream": frozenset({
        "camelot-stream-smart",
        "camelot-stream-global",
    }),
}

_REQUEST_KEYS = frozenset({
    "protocol",
    "operation",
    "request_id",
    "input_file",
    "flavor",
    "pages",
    "page_hint",
    "extractor",
    "region_prefix",
    "region_index_width",
    "table_area",
    "options",
})
_RESPONSE_KEYS = frozenset({
    "protocol",
    "operation",
    "request_id",
    "flavor",
    "pages",
    "page_hint",
    "extractor",
    "region_prefix",
    "region_index_width",
    "table_area",
    "tables",
})
_TABLE_KEYS = frozenset({
    "page",
    "bbox",
    "row_count",
    "column_count",
    "rows",
    "report",
})
_REPORT_KEYS = frozenset({"accuracy", "whitespace", "order", "page"})
_LATTICE_OPTIONS = frozenset({
    "strip_text",
    "dpi",
    "line_scale",
    "process_background",
    "copy_text",
    "shift_text",
})
_STREAM_OPTIONS = frozenset({
    "strip_text",
    "dpi",
    "columns",
})
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_SAFE_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_REGION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class WorkerRequestError(ValueError):
    """A solicitação não pertence ao protocolo interno permitido."""


class WorkerExtractionError(RuntimeError):
    """O Camelot não concluiu a extração."""


class WorkerResultError(RuntimeError):
    """O resultado não pode ser serializado com segurança."""


def _exact_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    if set(value) != set(allowed):
        raise WorkerRequestError(f"{label} possui campos inválidos.")


def _is_regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _is_within(parent: Path, child: Path, *, allow_equal: bool = False) -> bool:
    parent_real = parent.resolve()
    child_real = child.resolve()
    try:
        common = os.path.commonpath((str(parent_real), str(child_real)))
    except ValueError:
        return False
    return common == str(parent_real) and (
        allow_equal or child_real != parent_real
    )


def _safe_relative_path(workdir: Path, relative: str, *, must_exist: bool) -> Path:
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise WorkerRequestError("Caminho interno inválido.")
    candidate = workdir / relative
    if not _is_within(workdir, candidate):
        raise WorkerRequestError("Caminho interno fora do workdir.")
    if must_exist and not _is_regular_file(candidate):
        raise WorkerRequestError("Arquivo interno inválido.")
    return candidate


def parse_pages_spec(value: Any) -> set[int]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PAGES_SPEC_CHARS
    ):
        raise WorkerRequestError("Seleção de páginas inválida.")
    pages: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            raise WorkerRequestError("Seleção de páginas inválida.")
        if "-" in token:
            if token.count("-") != 1:
                raise WorkerRequestError("Seleção de páginas inválida.")
            first_raw, last_raw = token.split("-", 1)
            if not first_raw.isdigit() or not last_raw.isdigit():
                raise WorkerRequestError("Seleção de páginas inválida.")
            first, last = int(first_raw), int(last_raw)
            if first < 1 or last < first or last > MAX_PAGE_NUMBER:
                raise WorkerRequestError("Seleção de páginas inválida.")
            if last - first + 1 > MAX_REQUESTED_PAGES:
                raise WorkerRequestError("Seleção de páginas excessiva.")
            pages.update(range(first, last + 1))
        else:
            if not token.isdigit():
                raise WorkerRequestError("Seleção de páginas inválida.")
            page = int(token)
            if page < 1 or page > MAX_PAGE_NUMBER:
                raise WorkerRequestError("Seleção de páginas inválida.")
            pages.add(page)
        if len(pages) > MAX_REQUESTED_PAGES:
            raise WorkerRequestError("Seleção de páginas excessiva.")
    if not pages:
        raise WorkerRequestError("Seleção de páginas vazia.")
    return pages


def _coordinate_list(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> list[float]:
    if not isinstance(value, str) or len(value) > 1_024:
        raise WorkerRequestError(f"{label} inválida.")
    parts = [part.strip() for part in value.split(",")]
    if not minimum <= len(parts) <= maximum:
        raise WorkerRequestError(f"{label} inválida.")
    try:
        numbers = [float(part) for part in parts]
    except (TypeError, ValueError) as exc:
        raise WorkerRequestError(f"{label} inválida.") from exc
    if not all(math.isfinite(number) and abs(number) <= 1_000_000 for number in numbers):
        raise WorkerRequestError(f"{label} inválida.")
    return numbers


def _validate_table_area(value: Any) -> str | None:
    if value is None:
        return None
    numbers = _coordinate_list(
        value,
        minimum=4,
        maximum=4,
        label="Área",
    )
    if numbers[0] >= numbers[2] or numbers[1] <= numbers[3]:
        raise WorkerRequestError("Área inválida.")
    return value


def _validate_options(flavor: str, options: Any, table_area: str | None) -> Dict[str, Any]:
    if not isinstance(options, dict):
        raise WorkerRequestError("Opções inválidas.")
    allowed = _LATTICE_OPTIONS if flavor == "lattice" else _STREAM_OPTIONS
    unknown = set(options) - set(allowed)
    if unknown:
        raise WorkerRequestError("Opções desconhecidas.")

    normalized: Dict[str, Any] = {}
    strip_text = options.get("strip_text", "\n")
    if not isinstance(strip_text, str) or len(strip_text) > 8:
        raise WorkerRequestError("strip_text inválido.")
    normalized["strip_text"] = strip_text

    dpi = options.get("dpi", 200)
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise WorkerRequestError("DPI inválido.")
    normalized["dpi"] = dpi

    if flavor == "lattice":
        line_scale = options.get("line_scale", 80)
        if (
            isinstance(line_scale, bool)
            or not isinstance(line_scale, int)
            or not 1 <= line_scale <= 1_000
        ):
            raise WorkerRequestError("line_scale inválido.")
        normalized["line_scale"] = line_scale

        process_background = options.get("process_background", False)
        if not isinstance(process_background, bool):
            raise WorkerRequestError("process_background inválido.")
        normalized["process_background"] = process_background

        for key, allowed_values in (
            ("copy_text", {"h", "v"}),
            ("shift_text", {"l", "r", "t", "b"}),
        ):
            raw = options.get(key, ["h", "v"] if key == "copy_text" else ["l", "t"])
            if (
                not isinstance(raw, list)
                or not raw
                or len(raw) > 4
                or not all(
                    isinstance(item, str) and item in allowed_values
                    for item in raw
                )
            ):
                raise WorkerRequestError(f"{key} inválido.")
            normalized[key] = list(raw)
    else:
        columns = options.get("columns")
        if columns is not None:
            if table_area is None:
                raise WorkerRequestError("columns exige table_area.")
            column_positions = _coordinate_list(
                columns,
                minimum=1,
                maximum=MAX_COLUMNS - 1,
                label="Colunas",
            )
            if any(
                current <= previous
                for previous, current in zip(
                    column_positions,
                    column_positions[1:],
                )
            ):
                raise WorkerRequestError("Colunas inválidas.")
            normalized["columns"] = columns
    return normalized


def validate_request(payload: Any, workdir: str | os.PathLike[str]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise WorkerRequestError("Solicitação inválida.")
    _exact_keys(payload, _REQUEST_KEYS, "Solicitação")
    if payload["protocol"] != PROTOCOL_VERSION:
        raise WorkerRequestError("Versão de protocolo inválida.")
    if payload["operation"] != "extract":
        raise WorkerRequestError("Operação inválida.")
    if (
        not isinstance(payload["request_id"], str)
        or _SAFE_REQUEST_ID_RE.fullmatch(payload["request_id"]) is None
    ):
        raise WorkerRequestError("request_id inválido.")
    if payload["flavor"] not in {"lattice", "stream"}:
        raise WorkerRequestError("Flavor inválido.")
    if payload["extractor"] not in ALLOWED_EXTRACTORS:
        raise WorkerRequestError("Extrator inválido.")
    if payload["extractor"] not in ALLOWED_EXTRACTORS_BY_FLAVOR[
        payload["flavor"]
    ]:
        raise WorkerRequestError("Extrator incompatível com o flavor.")
    if (
        not isinstance(payload["region_prefix"], str)
        or _SAFE_REGION_RE.fullmatch(payload["region_prefix"]) is None
    ):
        raise WorkerRequestError("Região inválida.")
    width = payload["region_index_width"]
    if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 6:
        raise WorkerRequestError("Largura de índice inválida.")

    pages = parse_pages_spec(payload["pages"])
    page_hint = payload["page_hint"]
    if (
        isinstance(page_hint, bool)
        or not isinstance(page_hint, int)
        or page_hint not in pages
    ):
        raise WorkerRequestError("Página de referência inválida.")

    input_name = payload["input_file"]
    if (
        not isinstance(input_name, str)
        or _SAFE_NAME_RE.fullmatch(input_name) is None
        or input_name in {".", ".."}
        or Path(input_name).suffix.lower() != ".pdf"
    ):
        raise WorkerRequestError("Arquivo de entrada inválido.")
    workdir_path = Path(workdir).resolve()
    input_path = _safe_relative_path(
        workdir_path,
        input_name,
        must_exist=True,
    )
    table_area = _validate_table_area(payload["table_area"])
    options = _validate_options(
        payload["flavor"],
        payload["options"],
        table_area,
    )
    return {
        **payload,
        "input_path": input_path,
        "allowed_pages": pages,
        "table_area": table_area,
        "options": options,
    }


def _json_cell(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except Exception as exc:
            raise WorkerResultError("Tipo de célula inválido.") from exc
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, str):
        if len(value) > MAX_CELL_CHARS:
            raise WorkerResultError("Célula excede o limite.")
        return value
    raise WorkerResultError("Tipo de célula inválido.")


def _table_bbox(table: Any) -> list[float] | None:
    raw = getattr(table, "_bbox", None)
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 4:
        raise WorkerResultError("BBox inválida.")
    try:
        bbox = [float(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise WorkerResultError("BBox inválida.") from exc
    if not all(math.isfinite(value) and abs(value) <= 1_000_000 for value in bbox):
        raise WorkerResultError("BBox inválida.")
    if bbox[0] == bbox[2] or bbox[1] == bbox[3]:
        raise WorkerResultError("BBox inválida.")
    return bbox


def _safe_report(table: Any) -> Dict[str, float | int]:
    try:
        raw = getattr(table, "parsing_report", None) or {}
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    report: Dict[str, float | int] = {}
    for key in _REPORT_KEYS:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or abs(numeric) > 1_000_000:
            continue
        report[key] = int(value) if isinstance(value, int) else numeric
    return report


def _serialize_tables(collection: Any, request: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw_tables: Iterable[Any] = getattr(collection, "tables", collection)
    tables: list[Dict[str, Any]] = []
    total_rows = 0
    total_cells = 0
    for table in raw_tables:
        dataframe = getattr(table, "df", None)
        if dataframe is None or getattr(dataframe, "empty", True):
            continue
        try:
            row_count = int(dataframe.shape[0])
            column_count = int(dataframe.shape[1])
        except Exception as exc:
            raise WorkerResultError("Dimensões inválidas.") from exc
        if row_count <= 0 or column_count <= 0:
            continue
        if column_count > MAX_COLUMNS:
            raise WorkerResultError("Quantidade de colunas excessiva.")
        total_rows += row_count
        total_cells += row_count * column_count
        if (
            len(tables) >= MAX_TABLES
            or total_rows > MAX_TOTAL_ROWS
            or total_cells > MAX_TOTAL_CELLS
        ):
            raise WorkerResultError("Resultado excessivo.")

        rows = []
        try:
            iterator = dataframe.itertuples(index=False, name=None)
            for row in iterator:
                if len(row) != column_count:
                    raise WorkerResultError("Linha irregular.")
                rows.append([_json_cell(value) for value in row])
        except WorkerResultError:
            raise
        except Exception as exc:
            raise WorkerResultError("Falha ao serializar tabela.") from exc

        raw_page = getattr(table, "page", request["page_hint"])
        try:
            page = int(raw_page)
        except (TypeError, ValueError) as exc:
            raise WorkerResultError("Página inválida.") from exc
        if page not in request["allowed_pages"]:
            raise WorkerResultError("Página fora da solicitação.")

        tables.append({
            "page": page,
            "bbox": _table_bbox(table),
            "row_count": row_count,
            "column_count": column_count,
            "rows": rows,
            "report": _safe_report(table),
        })
    return tables


def _response_for(request: Mapping[str, Any], tables: list[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "operation": "extract",
        "request_id": request["request_id"],
        "flavor": request["flavor"],
        "pages": request["pages"],
        "page_hint": request["page_hint"],
        "extractor": request["extractor"],
        "region_prefix": request["region_prefix"],
        "region_index_width": request["region_index_width"],
        "table_area": request["table_area"],
        "tables": tables,
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if not encoded or len(encoded) > MAX_RESULT_BYTES:
        raise WorkerResultError("Resultado excede o limite.")
    fd, temporary = tempfile.mkstemp(
        prefix=".camelot-result-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def _load_request(path: Path) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise WorkerRequestError("Solicitação ilegível.") from exc
    if size <= 0 or size > MAX_REQUEST_BYTES:
        raise WorkerRequestError("Solicitação excessiva.")
    try:
        with path.open("r", encoding="utf-8", errors="strict") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkerRequestError("JSON de solicitação inválido.") from exc


def _camelot_kwargs(request: Mapping[str, Any]) -> Dict[str, Any]:
    options = dict(request["options"])
    columns = options.pop("columns", None)
    kwargs: Dict[str, Any] = {
        "flavor": request["flavor"],
        "pages": request["pages"],
        **options,
    }
    if request["table_area"] is not None:
        kwargs["table_areas"] = [request["table_area"]]
    if columns is not None:
        kwargs["columns"] = [columns]
    return kwargs


def execute_request(
    request_path: str,
    result_path: str,
    *,
    workdir: str | os.PathLike[str] | None = None,
) -> None:
    base = Path(workdir or os.getcwd()).resolve()
    request_file = _safe_relative_path(base, request_path, must_exist=True)
    result_file = _safe_relative_path(base, result_path, must_exist=False)
    if result_file.exists() or result_file.is_symlink():
        raise WorkerRequestError("Resultado interno já existe.")
    if not result_file.parent.is_dir() or result_file.parent.is_symlink():
        raise WorkerRequestError("Diretório de resultado inválido.")

    payload = _load_request(request_file)
    request = validate_request(payload, base)
    try:
        import camelot

        collection = camelot.read_pdf(
            str(request["input_path"]),
            **_camelot_kwargs(request),
        )
        tables = _serialize_tables(collection, request)
    except WorkerResultError:
        raise
    except Exception as exc:
        raise WorkerExtractionError("Falha na extração Camelot.") from exc
    _write_json_atomic(result_file, _response_for(request, tables))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camelot-worker",
        add_help=False,
    )
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        execute_request(args.request, args.result)
        return EXIT_OK
    except WorkerRequestError:
        return EXIT_REQUEST_INVALID
    except WorkerExtractionError:
        return EXIT_EXTRACTION_FAILED
    except WorkerResultError:
        return EXIT_RESULT_FAILED
    except Exception:
        return EXIT_RESULT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
