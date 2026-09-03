# -*- coding: utf-8 -*-
"""Mapeamento geométrico limitado e sem efeito no PDF -> XLSX."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


GeometryBBox = Tuple[float, float, float, float]
DeadlineCheck = Optional[Callable[[str], None]]

GEOMETRY_CLUSTER_TOLERANCE_PT = 1.25
GEOMETRY_INTERVAL_JOIN_TOLERANCE_PT = 2.0
GEOMETRY_MAX_BORDER_THICKNESS_PT = 1.5
GEOMETRY_MIN_VERTICAL_LENGTH_PT = 3.0
GEOMETRY_MIN_HORIZONTAL_LENGTH_PT = 3.0
GEOMETRY_MIN_REGION_COLUMNS = 2
GEOMETRY_MIN_REGION_ROWS = 1
GEOMETRY_MIN_RULE_OVERLAP = 0.55
GEOMETRY_MIN_VERTICAL_COVERAGE = 0.12
GEOMETRY_REGION_GAP_MULTIPLIER = 2.2
GEOMETRY_MIN_REGION_GAP_PT = 42.0
GEOMETRY_MAX_REGION_GAP_PT = 42.0
GEOMETRY_WORD_LINE_TOLERANCE_PT = 2.5

ROW_ROLES = frozenset({
    "title",
    "header",
    "section",
    "data",
    "total",
    "continuation",
    "note",
    "unknown",
})

_CODE_RE = re.compile(
    r"^(?=.{3,32}$)(?=.*\d)\d+(?:[.\-/]\d+){1,6}[A-Za-z]?$"
)
_NUMBER_RE = re.compile(
    r"^(?:R\$)?[+-]?(?:\d{1,3}(?:[.\s]\d{3})*|\d+)"
    r"(?:,\d+|\.\d+)?%?$",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(r"\b(?:total|subtotal)\b", re.IGNORECASE)
_NOTE_RE = re.compile(
    r"^\s*(?:obs(?:ervacoes?|ervacao)?\.?|nota)\s*[:.-]?",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(
    r"\b(?:tabela|quadro|coberturas?|procedimentos?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GeometryLimits:
    max_pages: int = 800
    max_objects_per_page: int = 20_000
    max_words_per_page: int = 10_000
    max_segments_per_page: int = 12_000
    max_regions_per_page: int = 32
    max_rows_per_region: int = 500
    max_columns_per_region: int = 64


DEFAULT_GEOMETRY_LIMITS = GeometryLimits()


@dataclass(frozen=True)
class ColumnBand:
    index: int
    x0: float
    x1: float


@dataclass(frozen=True)
class GeometryRow:
    index: int
    bbox: GeometryBBox
    role: str
    occupied_bands: Tuple[int, ...]
    numeric_band_count: int
    word_count: int
    visual_line_count: int
    has_code: bool
    merged: bool
    continuation_of: Optional[int]
    confidence: float


@dataclass(frozen=True)
class TableRegionGeometry:
    page_number: int
    region_index: int
    bbox: GeometryBBox
    column_edges: Tuple[float, ...]
    columns: Tuple[ColumnBand, ...]
    row_edges: Tuple[float, ...]
    rows: Tuple[GeometryRow, ...]
    confidence: float
    procedure_like: bool
    horizontal_rule_count: int
    vertical_rule_count: int

    def role_count(self, role: str) -> int:
        return sum(row.role == role for row in self.rows)


@dataclass(frozen=True)
class PageTableGeometry:
    page_number: int
    width: float
    height: float
    regions: Tuple[TableRegionGeometry, ...]
    external_rows: Tuple[GeometryRow, ...]
    line_count: int
    rect_count: int
    word_count: int
    segment_count: int
    confidence: float
    limited: bool = False
    fallback_reason: Optional[str] = None

    @property
    def note_count(self) -> int:
        return sum(row.role == "note" for row in self.external_rows)


@dataclass(frozen=True)
class CandidateGeometryReport:
    mapped: bool
    page_number: int
    region_index: Optional[int]
    region_bbox: Optional[GeometryBBox]
    column_band_count: int
    row_band_count: int
    title_count: int
    header_count: int
    section_count: int
    data_row_count: int
    total_count: int
    continuation_count: int
    note_count: int
    geometry_confidence: float
    ambiguous: bool = False


@dataclass(frozen=True)
class _Segment:
    orientation: str
    coordinate: float
    start: float
    end: float
    source: str


@dataclass(frozen=True)
class _AxisEvidence:
    coordinate: float
    intervals: Tuple[Tuple[float, float], ...]
    occurrences: int

    @property
    def coverage(self) -> float:
        return sum(end - start for start, end in self.intervals)


@dataclass(frozen=True)
class _HorizontalRule:
    y: float
    x0: float
    x1: float
    fragments: int


class GeometryLimitExceeded(RuntimeError):
    """Um limite do mapeamento sombra foi atingido."""


def _check_deadline(callback: DeadlineCheck, stage: str) -> None:
    if callback is not None:
        callback(stage)


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _object_box(
    item: Dict[str, Any],
    page_height: float,
) -> Optional[GeometryBBox]:
    x0 = _finite_number(item.get("x0"))
    x1 = _finite_number(item.get("x1"))
    y0 = _finite_number(item.get("y0"))
    y1 = _finite_number(item.get("y1"))
    if y0 is None or y1 is None:
        top = _finite_number(item.get("top"))
        bottom = _finite_number(item.get("bottom"))
        if top is None or bottom is None:
            return None
        y0, y1 = page_height - bottom, page_height - top
    if x0 is None or x1 is None:
        return None
    left, right = sorted((x0, x1))
    bottom_y, top_y = sorted((y0, y1))
    if not all(
        abs(value) <= 1_000_000
        for value in (left, bottom_y, right, top_y)
    ):
        return None
    return left, bottom_y, right, top_y


def _segments_from_page_objects(
    lines: Sequence[Dict[str, Any]],
    rects: Sequence[Dict[str, Any]],
    page_width: float,
    page_height: float,
    *,
    limits: GeometryLimits,
    check_deadline: DeadlineCheck,
) -> List[_Segment]:
    segments: List[_Segment] = []

    def append_segment(
        orientation: str,
        coordinate: float,
        start: float,
        end: float,
        source: str,
    ) -> None:
        if len(segments) >= limits.max_segments_per_page:
            raise GeometryLimitExceeded("segment_limit")
        low, high = sorted((start, end))
        if high <= low:
            return
        segments.append(_Segment(
            orientation=orientation,
            coordinate=coordinate,
            start=low,
            end=high,
            source=source,
        ))

    for index, item in enumerate(lines):
        if index % 256 == 0:
            _check_deadline(check_deadline, "pdf-xlsx-geometry-lines")
        box = _object_box(item, page_height)
        if box is None:
            continue
        x0, y0, x1, y1 = box
        width, height = x1 - x0, y1 - y0
        if (
            height <= GEOMETRY_MAX_BORDER_THICKNESS_PT
            and width >= GEOMETRY_MIN_HORIZONTAL_LENGTH_PT
        ):
            append_segment(
                "horizontal",
                (y0 + y1) / 2.0,
                x0,
                x1,
                "line",
            )
        elif (
            width <= GEOMETRY_MAX_BORDER_THICKNESS_PT
            and height >= GEOMETRY_MIN_VERTICAL_LENGTH_PT
        ):
            append_segment(
                "vertical",
                (x0 + x1) / 2.0,
                y0,
                y1,
                "line",
            )

    for index, item in enumerate(rects):
        if index % 256 == 0:
            _check_deadline(check_deadline, "pdf-xlsx-geometry-rects")
        box = _object_box(item, page_height)
        if box is None:
            continue
        x0, y0, x1, y1 = box
        width, height = x1 - x0, y1 - y0
        if (
            height <= GEOMETRY_MAX_BORDER_THICKNESS_PT
            and width >= max(
                GEOMETRY_MIN_HORIZONTAL_LENGTH_PT,
                page_width * 0.005,
            )
        ):
            append_segment(
                "horizontal",
                (y0 + y1) / 2.0,
                x0,
                x1,
                "rect",
            )
        elif (
            width <= GEOMETRY_MAX_BORDER_THICKNESS_PT
            and height >= GEOMETRY_MIN_VERTICAL_LENGTH_PT
        ):
            append_segment(
                "vertical",
                (x0 + x1) / 2.0,
                y0,
                y1,
                "rect",
            )
    return segments


def _cluster_values(
    values: Iterable[float],
    *,
    tolerance: float = GEOMETRY_CLUSTER_TOLERANCE_PT,
) -> List[List[float]]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return []
    clusters: List[List[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        cluster = clusters[-1]
        center = sum(cluster) / len(cluster)
        if abs(value - center) <= tolerance:
            cluster.append(value)
        else:
            clusters.append([value])
    return clusters


def cluster_coordinates(
    values: Iterable[float],
    *,
    tolerance: float = GEOMETRY_CLUSTER_TOLERANCE_PT,
) -> Tuple[float, ...]:
    """API pequena e determinística usada também pelos testes sintéticos."""
    return tuple(
        round(sum(cluster) / len(cluster), 6)
        for cluster in _cluster_values(values, tolerance=tolerance)
    )


def _merge_intervals(
    intervals: Iterable[Tuple[float, float]],
    *,
    tolerance: float = GEOMETRY_INTERVAL_JOIN_TOLERANCE_PT,
) -> Tuple[Tuple[float, float], ...]:
    ordered = sorted(
        (min(start, end), max(start, end))
        for start, end in intervals
        if end != start
    )
    merged: List[List[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + tolerance:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _axis_evidence(
    segments: Sequence[_Segment],
    orientation: str,
    *,
    check_deadline: DeadlineCheck,
) -> List[_AxisEvidence]:
    selected = [
        segment for segment in segments
        if segment.orientation == orientation
    ]
    clusters = _cluster_values(segment.coordinate for segment in selected)
    evidence: List[_AxisEvidence] = []
    for index, coordinates in enumerate(clusters):
        if index % 128 == 0:
            _check_deadline(check_deadline, "pdf-xlsx-geometry-cluster")
        center = sum(coordinates) / len(coordinates)
        members = [
            segment
            for segment in selected
            if abs(segment.coordinate - center)
            <= GEOMETRY_CLUSTER_TOLERANCE_PT
        ]
        evidence.append(_AxisEvidence(
            coordinate=round(
                sum(segment.coordinate for segment in members) / len(members),
                6,
            ),
            intervals=_merge_intervals(
                (segment.start, segment.end) for segment in members
            ),
            occurrences=len(members),
        ))
    return evidence


def _horizontal_rules(
    evidence: Sequence[_AxisEvidence],
    page_width: float,
) -> List[_HorizontalRule]:
    rules: List[_HorizontalRule] = []
    minimum_width = max(
        GEOMETRY_MIN_HORIZONTAL_LENGTH_PT,
        page_width * 0.02,
    )
    for item in evidence:
        for start, end in item.intervals:
            if end - start < minimum_width:
                continue
            rules.append(_HorizontalRule(
                y=item.coordinate,
                x0=start,
                x1=end,
                fragments=item.occurrences,
            ))
    return sorted(rules, key=lambda rule: (-rule.y, rule.x0, rule.x1))


def _interval_overlap(
    first: Tuple[float, float],
    second: Tuple[float, float],
) -> float:
    return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))


def _rule_overlap_ratio(
    first: _HorizontalRule,
    second: _HorizontalRule,
) -> float:
    overlap = _interval_overlap(
        (first.x0, first.x1),
        (second.x0, second.x1),
    )
    smaller = min(first.x1 - first.x0, second.x1 - second.x0)
    return overlap / smaller if smaller > 0 else 0.0


def _region_gap_threshold(rules: Sequence[_HorizontalRule]) -> float:
    unique_y = sorted({rule.y for rule in rules}, reverse=True)
    gaps = [
        first - second
        for first, second in zip(unique_y, unique_y[1:])
        if first > second
    ]
    if not gaps:
        return GEOMETRY_MIN_REGION_GAP_PT
    ordered = sorted(gaps)
    median = ordered[len(ordered) // 2]
    return min(
        GEOMETRY_MAX_REGION_GAP_PT,
        max(
            GEOMETRY_MIN_REGION_GAP_PT,
            median * GEOMETRY_REGION_GAP_MULTIPLIER,
        ),
    )


def _group_horizontal_rules(
    rules: Sequence[_HorizontalRule],
) -> List[List[_HorizontalRule]]:
    if not rules:
        return []
    max_gap = _region_gap_threshold(rules)
    groups: List[List[_HorizontalRule]] = []
    anchors: List[_HorizontalRule] = []
    last_y: List[float] = []
    for rule in rules:
        matched = None
        for index, anchor in enumerate(anchors):
            gap = last_y[index] - rule.y
            if gap < -GEOMETRY_CLUSTER_TOLERANCE_PT or gap > max_gap:
                continue
            if _rule_overlap_ratio(anchor, rule) >= GEOMETRY_MIN_RULE_OVERLAP:
                matched = index
                break
        if matched is None:
            groups.append([rule])
            anchors.append(rule)
            last_y.append(rule.y)
        else:
            groups[matched].append(rule)
            last_y[matched] = min(last_y[matched], rule.y)
    return groups


def _intersection_coverage(
    intervals: Sequence[Tuple[float, float]],
    low: float,
    high: float,
) -> float:
    clipped = [
        (max(low, start), min(high, end))
        for start, end in intervals
        if min(high, end) > max(low, start)
    ]
    return sum(end - start for start, end in _merge_intervals(clipped))


def _vertical_edges_for_region(
    vertical: Sequence[_AxisEvidence],
    rules: Sequence[_HorizontalRule],
) -> Tuple[float, ...]:
    y_values = [rule.y for rule in rules]
    if len(set(y_values)) < 2:
        return ()
    low, high = min(y_values), max(y_values)
    region_height = high - low
    x0 = min(rule.x0 for rule in rules)
    x1 = max(rule.x1 for rule in rules)
    accepted: List[float] = []
    for item in vertical:
        if not x0 - 2.0 <= item.coordinate <= x1 + 2.0:
            continue
        coverage = _intersection_coverage(item.intervals, low, high)
        crossings = sum(
            rule.x0 - 1.0 <= item.coordinate <= rule.x1 + 1.0
            and any(
                start - 1.0 <= rule.y <= end + 1.0
                for start, end in item.intervals
            )
            for rule in rules
        )
        if (
            item.occurrences >= 2
            and (
                coverage >= max(
                    GEOMETRY_MIN_VERTICAL_LENGTH_PT,
                    region_height * GEOMETRY_MIN_VERTICAL_COVERAGE,
                )
                or crossings >= 2
            )
        ):
            accepted.append(item.coordinate)
    return cluster_coordinates(accepted)


def _word_box(
    word: Dict[str, Any],
    page_height: float,
) -> Optional[GeometryBBox]:
    return _object_box(word, page_height)


def _normalize_signal_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    ).strip()


def _word_center(
    word: Dict[str, Any],
    page_height: float,
) -> Optional[Tuple[float, float, GeometryBBox]]:
    box = _word_box(word, page_height)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0, box


def _band_index(edges: Sequence[float], x: float) -> Optional[int]:
    for index, (left, right) in enumerate(zip(edges, edges[1:])):
        if left - 0.5 <= x <= right + 0.5:
            return index
    return None


def _visual_line_count(
    words: Sequence[Dict[str, Any]],
    page_height: float,
) -> int:
    centers = []
    for word in words:
        centered = _word_center(word, page_height)
        if centered is not None:
            centers.append(centered[1])
    return max(
        1 if words else 0,
        len(_cluster_values(
            centers,
            tolerance=GEOMETRY_WORD_LINE_TOLERANCE_PT,
        )),
    )


def _vertical_covers_row(
    item: _AxisEvidence,
    low: float,
    high: float,
) -> bool:
    required = max(1.0, (high - low) * 0.55)
    return _intersection_coverage(item.intervals, low, high) >= required


def _row_features(
    words: Sequence[Dict[str, Any]],
    edges: Sequence[float],
    vertical: Sequence[_AxisEvidence],
    low: float,
    high: float,
    page_height: float,
) -> Dict[str, Any]:
    occupied = set()
    numeric_bands = set()
    first_band_tokens: List[str] = []
    signal_parts: List[str] = []
    for word in words:
        centered = _word_center(word, page_height)
        if centered is None:
            continue
        x, _y, _box = centered
        band = _band_index(edges, x)
        text = _normalize_signal_text(word.get("text"))
        if band is not None:
            occupied.add(band)
            if band == 0 and text:
                first_band_tokens.append(text)
            if band > 0 and _NUMBER_RE.fullmatch(
                text.replace(" ", "")
            ):
                numeric_bands.add(band)
        if text:
            signal_parts.append(text)
    signal_text = " ".join(signal_parts)
    internal_edges = edges[1:-1]
    active_internal = sum(
        item is not None
        and abs(item.coordinate - edge)
        <= GEOMETRY_CLUSTER_TOLERANCE_PT * 1.5
        and _vertical_covers_row(item, low, high)
        for edge in internal_edges
        for item in [
            min(
                vertical,
                key=lambda candidate: abs(candidate.coordinate - edge),
                default=None,
            )
        ]
    )
    merged = bool(internal_edges) and active_internal <= max(
        0,
        len(internal_edges) // 3,
    )
    return {
        "occupied": tuple(sorted(occupied)),
        "numeric_band_count": len(numeric_bands),
        "has_code": any(
            _CODE_RE.fullmatch(token.replace(" ", ""))
            for token in first_band_tokens
        ),
        "merged": merged,
        "total_signal": bool(_TOTAL_RE.search(signal_text)),
        "note_signal": bool(_NOTE_RE.search(signal_text)),
        "title_signal": bool(_TITLE_RE.search(signal_text)),
        "word_count": len(words),
        "visual_line_count": _visual_line_count(words, page_height),
    }


def _words_in_band(
    words: Sequence[Dict[str, Any]],
    bbox: GeometryBBox,
    page_height: float,
) -> List[Dict[str, Any]]:
    x0, y0, x1, y1 = bbox
    selected = []
    for word in words:
        centered = _word_center(word, page_height)
        if centered is None:
            continue
        x, y, _box = centered
        if x0 - 1.0 <= x <= x1 + 1.0 and y0 <= y <= y1:
            selected.append(word)
    return selected


def _classify_rows(
    page_number: int,
    region_index: int,
    edges: Sequence[float],
    row_edges: Sequence[float],
    words: Sequence[Dict[str, Any]],
    vertical: Sequence[_AxisEvidence],
    page_height: float,
) -> Tuple[GeometryRow, ...]:
    rows: List[GeometryRow] = []
    first_data_seen = False
    previous_data_index: Optional[int] = None
    header_window_open = True
    for index, (upper, lower) in enumerate(
        zip(row_edges, row_edges[1:])
    ):
        bbox = (edges[0], lower, edges[-1], upper)
        row_words = _words_in_band(words, bbox, page_height)
        features = _row_features(
            row_words,
            edges,
            vertical,
            lower,
            upper,
            page_height,
        )
        occupied_count = len(features["occupied"])
        has_code = features["has_code"]
        numeric_count = features["numeric_band_count"]
        merged = features["merged"]
        continuation_of = None

        if not row_words:
            role = "unknown"
            confidence = 0.2
        elif (
            features["note_signal"]
            and merged
            and numeric_count == 0
        ):
            role = "note"
            confidence = 0.8
        elif (
            features["total_signal"]
            and not has_code
            and numeric_count >= 1
        ):
            role = "total"
            confidence = 0.95
            header_window_open = False
        elif has_code and (numeric_count >= 1 or occupied_count >= 3):
            role = "data"
            confidence = 0.95 if numeric_count >= 1 else 0.8
            first_data_seen = True
            previous_data_index = index
            header_window_open = False
        elif (
            merged
            and not has_code
            and numeric_count == 0
            and occupied_count >= 1
        ):
            if (
                not first_data_seen
                and (
                    features["title_signal"]
                    or not rows
                )
            ):
                role = "title"
                confidence = 0.8
            elif (
                not first_data_seen
                and header_window_open
                and len(rows) == 1
            ):
                role = "header"
                confidence = 0.8
            else:
                role = "section"
                confidence = 0.85
            previous_data_index = None
            header_window_open = True
        elif (
            header_window_open
            and occupied_count >= 2
            and not has_code
        ):
            role = "header"
            confidence = 0.8
        elif (
            previous_data_index is not None
            and not has_code
            and numeric_count == 0
            and occupied_count
            and set(features["occupied"]).issubset({0, 1, 2})
        ):
            role = "continuation"
            continuation_of = previous_data_index
            confidence = 0.8
        else:
            role = "unknown"
            confidence = 0.4

        rows.append(GeometryRow(
            index=index,
            bbox=bbox,
            role=role,
            occupied_bands=features["occupied"],
            numeric_band_count=numeric_count,
            word_count=features["word_count"],
            visual_line_count=features["visual_line_count"],
            has_code=has_code,
            merged=merged,
            continuation_of=continuation_of,
            confidence=confidence,
        ))
    return tuple(rows)


def _region_from_rules(
    page_number: int,
    region_index: int,
    rules: Sequence[_HorizontalRule],
    vertical: Sequence[_AxisEvidence],
    words: Sequence[Dict[str, Any]],
    page_height: float,
    *,
    limits: GeometryLimits,
) -> Optional[TableRegionGeometry]:
    row_edges = tuple(sorted({rule.y for rule in rules}, reverse=True))
    if len(row_edges) < GEOMETRY_MIN_REGION_ROWS + 1:
        return None
    if len(row_edges) - 1 > limits.max_rows_per_region:
        raise GeometryLimitExceeded("row_limit")
    column_edges = _vertical_edges_for_region(vertical, rules)
    if len(column_edges) < GEOMETRY_MIN_REGION_COLUMNS + 1:
        return None
    if len(column_edges) - 1 > limits.max_columns_per_region:
        raise GeometryLimitExceeded("column_limit")
    columns = tuple(
        ColumnBand(index=index, x0=x0, x1=x1)
        for index, (x0, x1) in enumerate(
            zip(column_edges, column_edges[1:])
        )
    )
    rows = _classify_rows(
        page_number,
        region_index,
        column_edges,
        row_edges,
        words,
        vertical,
        page_height,
    )
    bbox = (
        column_edges[0],
        row_edges[-1],
        column_edges[-1],
        row_edges[0],
    )
    classified = sum(row.role != "unknown" for row in rows)
    confidence = min(
        1.0,
        0.35
        + min(0.25, len(columns) * 0.04)
        + min(0.20, len(rows) * 0.01)
        + (classified / len(rows) * 0.20 if rows else 0.0),
    )
    data_rows = [row for row in rows if row.role == "data"]
    procedure_like = (
        len(columns) >= 3
        and len(data_rows) >= 2
        and sum(row.has_code for row in data_rows)
        >= math.ceil(len(data_rows) * 0.6)
        and sum(row.numeric_band_count > 0 for row in data_rows)
        >= math.ceil(len(data_rows) * 0.6)
    )
    return TableRegionGeometry(
        page_number=page_number,
        region_index=region_index,
        bbox=bbox,
        column_edges=tuple(column_edges),
        columns=columns,
        row_edges=row_edges,
        rows=rows,
        confidence=round(confidence, 6),
        procedure_like=procedure_like,
        horizontal_rule_count=len(rules),
        vertical_rule_count=len(column_edges),
    )


def _outside_all_regions(
    word: Dict[str, Any],
    regions: Sequence[TableRegionGeometry],
    page_height: float,
) -> bool:
    centered = _word_center(word, page_height)
    if centered is None:
        return False
    x, y, _box = centered
    return not any(
        region.bbox[0] <= x <= region.bbox[2]
        and region.bbox[1] <= y <= region.bbox[3]
        for region in regions
    )


def _external_note_rows(
    words: Sequence[Dict[str, Any]],
    regions: Sequence[TableRegionGeometry],
    page_height: float,
) -> Tuple[GeometryRow, ...]:
    if not regions:
        return ()
    outside = [
        word
        for word in words
        if _outside_all_regions(word, regions, page_height)
    ]
    if not outside:
        return ()
    line_clusters = _cluster_values(
        (
            centered[1]
            for word in outside
            for centered in [_word_center(word, page_height)]
            if centered is not None
        ),
        tolerance=GEOMETRY_WORD_LINE_TOLERANCE_PT,
    )
    line_centers = sorted(
        (sum(cluster) / len(cluster) for cluster in line_clusters),
        reverse=True,
    )
    lines: List[List[Dict[str, Any]]] = []
    for center in line_centers:
        line = [
            word
            for word in outside
            if (
                (position := _word_center(word, page_height)) is not None
                and abs(position[1] - center)
                <= GEOMETRY_WORD_LINE_TOLERANCE_PT
            )
        ]
        if line:
            lines.append(line)

    notes: List[GeometryRow] = []
    lowest_grid_y = min(region.bbox[1] for region in regions)
    index = 0
    while index < len(lines):
        line = lines[index]
        signal = " ".join(
            _normalize_signal_text(word.get("text"))
            for word in line
        )
        line_centers = [
            _word_center(word, page_height)[1]
            for word in line
            if _word_center(word, page_height) is not None
        ]
        first_line_y = (
            sum(line_centers) / len(line_centers)
            if line_centers
            else page_height
        )
        next_line_is_close = False
        if index + 1 < len(lines):
            next_centers = [
                _word_center(word, page_height)[1]
                for word in lines[index + 1]
                if _word_center(word, page_height) is not None
            ]
            next_line_is_close = bool(
                next_centers
                and first_line_y - (
                    sum(next_centers) / len(next_centers)
                ) <= 18.0
            )
        structural_note_below_grid = (
            first_line_y < lowest_grid_y
            and lowest_grid_y - first_line_y <= 36.0
            and next_line_is_close
        )
        if (
            not _NOTE_RE.search(signal)
            and not structural_note_below_grid
        ):
            index += 1
            continue
        paragraph = list(line)
        first_y = max(
            _word_center(word, page_height)[1]
            for word in line
            if _word_center(word, page_height) is not None
        )
        last_y = min(
            _word_center(word, page_height)[1]
            for word in line
            if _word_center(word, page_height) is not None
        )
        following = index + 1
        while following < len(lines):
            next_line = lines[following]
            next_centers = [
                _word_center(word, page_height)[1]
                for word in next_line
                if _word_center(word, page_height) is not None
            ]
            if not next_centers:
                break
            next_y = sum(next_centers) / len(next_centers)
            if last_y - next_y > 18.0:
                break
            paragraph.extend(next_line)
            last_y = min(last_y, next_y)
            following += 1
        boxes = [
            box
            for word in paragraph
            for box in [_word_box(word, page_height)]
            if box is not None
        ]
        if boxes:
            bbox = (
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            )
            notes.append(GeometryRow(
                index=len(notes),
                bbox=bbox,
                role="note",
                occupied_bands=(),
                numeric_band_count=0,
                word_count=len(paragraph),
                visual_line_count=max(1, following - index),
                has_code=False,
                merged=True,
                continuation_of=None,
                confidence=0.85,
            ))
        index = max(index + 1, following)
    return tuple(notes)


def empty_page_geometry(
    page_number: int,
    width: float,
    height: float,
    *,
    line_count: int = 0,
    rect_count: int = 0,
    word_count: int = 0,
    segment_count: int = 0,
    limited: bool = False,
    fallback_reason: Optional[str] = None,
) -> PageTableGeometry:
    return PageTableGeometry(
        page_number=page_number,
        width=width,
        height=height,
        regions=(),
        external_rows=(),
        line_count=line_count,
        rect_count=rect_count,
        word_count=word_count,
        segment_count=segment_count,
        confidence=0.0,
        limited=limited,
        fallback_reason=fallback_reason,
    )


def map_page_table_geometry(
    page: Any,
    page_number: int,
    *,
    check_deadline: DeadlineCheck = None,
    limits: GeometryLimits = DEFAULT_GEOMETRY_LIMITS,
) -> PageTableGeometry:
    _check_deadline(check_deadline, "pdf-xlsx-geometry-page")
    width = float(page.width)
    height = float(page.height)
    lines = list(page.lines or [])
    rects = list(page.rects or [])
    object_count = len(lines) + len(rects)
    if object_count > limits.max_objects_per_page:
        return empty_page_geometry(
            page_number,
            width,
            height,
            line_count=len(lines),
            rect_count=len(rects),
            limited=True,
            fallback_reason="object_limit",
        )
    _check_deadline(check_deadline, "pdf-xlsx-geometry-before-objects")
    try:
        segments = _segments_from_page_objects(
            lines,
            rects,
            width,
            height,
            limits=limits,
            check_deadline=check_deadline,
        )
    except GeometryLimitExceeded as exc:
        return empty_page_geometry(
            page_number,
            width,
            height,
            line_count=len(lines),
            rect_count=len(rects),
            limited=True,
            fallback_reason=str(exc),
        )
    _check_deadline(check_deadline, "pdf-xlsx-geometry-after-objects")
    words = list(page.extract_words() or [])
    if len(words) > limits.max_words_per_page:
        return empty_page_geometry(
            page_number,
            width,
            height,
            line_count=len(lines),
            rect_count=len(rects),
            word_count=len(words),
            segment_count=len(segments),
            limited=True,
            fallback_reason="word_limit",
        )
    if not segments:
        return empty_page_geometry(
            page_number,
            width,
            height,
            line_count=len(lines),
            rect_count=len(rects),
            word_count=len(words),
        )

    horizontal = _axis_evidence(
        segments,
        "horizontal",
        check_deadline=check_deadline,
    )
    vertical = _axis_evidence(
        segments,
        "vertical",
        check_deadline=check_deadline,
    )
    rules = _horizontal_rules(horizontal, width)
    groups = _group_horizontal_rules(rules)
    regions: List[TableRegionGeometry] = []
    try:
        for group in groups:
            _check_deadline(check_deadline, "pdf-xlsx-geometry-region")
            region = _region_from_rules(
                page_number,
                len(regions) + 1,
                group,
                vertical,
                words,
                height,
                limits=limits,
            )
            if region is None:
                continue
            regions.append(region)
            if len(regions) > limits.max_regions_per_page:
                raise GeometryLimitExceeded("region_limit")
    except GeometryLimitExceeded as exc:
        return empty_page_geometry(
            page_number,
            width,
            height,
            line_count=len(lines),
            rect_count=len(rects),
            word_count=len(words),
            segment_count=len(segments),
            limited=True,
            fallback_reason=str(exc),
        )
    external_rows = _external_note_rows(words, regions, height)
    confidence = (
        sum(region.confidence for region in regions) / len(regions)
        if regions
        else 0.0
    )
    return PageTableGeometry(
        page_number=page_number,
        width=width,
        height=height,
        regions=tuple(regions),
        external_rows=external_rows,
        line_count=len(lines),
        rect_count=len(rects),
        word_count=len(words),
        segment_count=len(segments),
        confidence=round(confidence, 6),
    )


def map_pdf_table_geometry(
    pdf_path: str,
    pages: Sequence[int],
    *,
    check_deadline: DeadlineCheck = None,
    limits: GeometryLimits = DEFAULT_GEOMETRY_LIMITS,
) -> Dict[int, PageTableGeometry]:
    import pdfplumber

    _check_deadline(check_deadline, "pdf-xlsx-geometry-before-open")
    requested = sorted(set(int(page) for page in pages if int(page) > 0))
    if len(requested) > limits.max_pages:
        return {}
    requested_set = set(requested)
    mapped: Dict[int, PageTableGeometry] = {}
    with pdfplumber.open(pdf_path) as document:
        for page_number, page in enumerate(document.pages, start=1):
            if page_number not in requested_set:
                continue
            _check_deadline(check_deadline, "pdf-xlsx-geometry-pdf-page")
            mapped[page_number] = map_page_table_geometry(
                page,
                page_number,
                check_deadline=check_deadline,
                limits=limits,
            )
    _check_deadline(check_deadline, "pdf-xlsx-geometry-after-map")
    return mapped


def bbox_overlap_metrics(
    first: GeometryBBox,
    second: GeometryBBox,
) -> Tuple[float, float]:
    left = max(first[0], second[0])
    bottom = max(first[1], second[1])
    right = min(first[2], second[2])
    top = min(first[3], second[3])
    if right <= left or top <= bottom:
        return 0.0, 0.0
    intersection = (right - left) * (top - bottom)
    first_area = max(0.0, (first[2] - first[0]) * (first[3] - first[1]))
    second_area = max(
        0.0,
        (second[2] - second[0]) * (second[3] - second[1]),
    )
    union = first_area + second_area - intersection
    iou = intersection / union if union else 0.0
    smaller = min(first_area, second_area)
    coverage = intersection / smaller if smaller else 0.0
    return iou, coverage


def geometry_report_for_candidate(
    page: Optional[PageTableGeometry],
    candidate_bbox: Optional[GeometryBBox],
) -> CandidateGeometryReport:
    if page is None or not page.regions or candidate_bbox is None:
        return CandidateGeometryReport(
            mapped=False,
            page_number=page.page_number if page is not None else 0,
            region_index=None,
            region_bbox=None,
            column_band_count=0,
            row_band_count=0,
            title_count=0,
            header_count=0,
            section_count=0,
            data_row_count=0,
            total_count=0,
            continuation_count=0,
            note_count=page.note_count if page is not None else 0,
            geometry_confidence=page.confidence if page is not None else 0.0,
        )
    matches = []
    for region in page.regions:
        iou, coverage = bbox_overlap_metrics(candidate_bbox, region.bbox)
        if coverage >= 0.35 or iou >= 0.15:
            matches.append((coverage, iou, region))
    if not matches:
        return CandidateGeometryReport(
            mapped=False,
            page_number=page.page_number,
            region_index=None,
            region_bbox=None,
            column_band_count=0,
            row_band_count=0,
            title_count=0,
            header_count=0,
            section_count=0,
            data_row_count=0,
            total_count=0,
            continuation_count=0,
            note_count=page.note_count,
            geometry_confidence=page.confidence,
        )
    matches.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2].confidence,
            -item[2].region_index,
        ),
        reverse=True,
    )
    materially_covered = [
        item for item in matches if item[0] >= 0.80
    ]
    if len(materially_covered) > 1:
        return CandidateGeometryReport(
            mapped=False,
            page_number=page.page_number,
            region_index=None,
            region_bbox=None,
            column_band_count=0,
            row_band_count=0,
            title_count=0,
            header_count=0,
            section_count=0,
            data_row_count=0,
            total_count=0,
            continuation_count=0,
            note_count=page.note_count,
            geometry_confidence=page.confidence,
            ambiguous=True,
        )
    region = matches[0][2]
    return CandidateGeometryReport(
        mapped=True,
        page_number=page.page_number,
        region_index=region.region_index,
        region_bbox=region.bbox,
        column_band_count=len(region.columns),
        row_band_count=len(region.rows),
        title_count=region.role_count("title"),
        header_count=region.role_count("header"),
        section_count=region.role_count("section"),
        data_row_count=region.role_count("data"),
        total_count=region.role_count("total"),
        continuation_count=region.role_count("continuation"),
        note_count=page.note_count + region.role_count("note"),
        geometry_confidence=region.confidence,
    )
