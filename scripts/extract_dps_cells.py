#!/usr/bin/env python3
"""Extract review-only DPS table cells from the reference image.

This is deliberately independent from ``reparse_dps.py``.  It consumes its existing raw OCR
tokens and broad table candidates, re-measures each table's grid directly from the source
pixels, then runs RapidOCR recognition only on individual grid cells.  The result is a
candidate transcription for manual review; it never changes optimizer or demo data.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import cv2
import numpy as np
if TYPE_CHECKING:
    from rapidocr import RapidOCR


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/public/reference-dps.jpg"
DEFAULT_RECOGNITION = ROOT / "data/dps-recognition-v3.5.2.json"
DEFAULT_OUTPUT = ROOT / "work/dps-cell-extraction/dps-cell-candidates.json"


def log(message: str) -> None:
    print(message, flush=True)


def rect(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}


def bounds(box: list[list[int]]) -> tuple[int, int, int, int]:
    points = np.asarray(box)
    x1, y1 = points.min(axis=0)
    x2, y2 = points.max(axis=0)
    return int(x1), int(y1), int(x2), int(y2)


def center(box: list[list[int]]) -> tuple[float, float]:
    x1, y1, x2, y2 = bounds(box)
    return (x1 + x2) / 2, (y1 + y2) / 2


def group_indices(indices: Iterable[int]) -> list[int]:
    """Return the center index of each contiguous run of line pixels."""
    values = list(map(int, indices))
    if not values:
        return []
    groups: list[list[int]] = [[values[0]]]
    for value in values[1:]:
        # Canny often emits the two sides of a 1--2 px coloured grid stroke with a tiny gap.
        if value <= groups[-1][-1] + 3:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [int(round(sum(group) / len(group))) for group in groups]


def line_centers(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Locate long grid strokes in a local image crop using Canny plus morphology."""
    edges = cv2.Canny(gray, 40, 120)
    vertical = cv2.morphologyEx(
        edges,
        cv2.MORPH_OPEN,
        # Coloured cell borders can be interrupted at horizontal intersections; a 25--30 px
        # run still distinguishes a grid stroke while retaining those otherwise missed lines.
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(24, gray.shape[0] // 35))),
    )
    horizontal = cv2.morphologyEx(
        edges,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(28, gray.shape[1] // 12), 1)),
    )
    vertical_threshold = max(20, int(gray.shape[0] * 0.12)) * 255
    horizontal_threshold = max(20, int(gray.shape[1] * 0.18)) * 255
    xs = group_indices(np.flatnonzero(vertical.sum(axis=0) >= vertical_threshold))
    ys = group_indices(np.flatnonzero(horizontal.sum(axis=1) >= horizontal_threshold))
    return xs, ys


def inside(point: tuple[float, float], area: dict[str, int], pad: int = 0) -> bool:
    x, y = point
    return area["x"] - pad <= x <= area["x"] + area["width"] + pad and area["y"] - pad <= y <= area["y"] + area["height"] + pad


def tokens_in_rect(tokens: Iterable[dict[str, Any]], area: dict[str, int]) -> list[dict[str, Any]]:
    return [token for token in tokens if inside(center(token["box"]), area, pad=1)]


def nearest_before(values: list[int], target: float) -> int | None:
    valid = [value for value in values if value < target]
    return max(valid) if valid else None


def nearest_after(values: list[int], target: float) -> int | None:
    valid = [value for value in values if value > target]
    return min(valid) if valid else None


def contiguous_from_start(lines: list[int], start: int) -> list[int]:
    """Keep table row borders, stopping at a decorative area after a large gap."""
    result = [start]
    for line in [line for line in lines if line > start + 12]:
        gap = line - result[-1]
        if 20 <= gap <= 120:
            result.append(line)
        elif gap > 120:
            break
    return result


def refine_grid(
    image: np.ndarray,
    candidate: dict[str, Any],
    panel_rect: dict[str, int],
    tokens: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure one table grid from source pixels, without assuming a fixed table height."""
    candidate_rect = candidate["source_rect"]
    header = candidate["header_token"]
    header_center_x, header_center_y = center(header["box"])
    image_height, image_width = image.shape[:2]
    search_x1 = max(0, candidate_rect["x"] - 24)
    search_x2 = min(image_width, candidate_rect["x"] + candidate_rect["width"] + 24)
    search_y1 = max(0, candidate_rect["y"] - 65)
    search_y2 = min(image_height, panel_rect["y"] + panel_rect["height"])
    gray = cv2.cvtColor(image[search_y1:search_y2, search_x1:search_x2], cv2.COLOR_BGR2GRAY)
    local_xs, local_ys = line_centers(gray)
    # A few teal grids are nearly the same luminance as their cell fill.  Their border edges
    # are still visible in a low-threshold Canny projection but are too interrupted for the
    # morphology pass above.  Use that projection only as a failure recovery, so text strokes
    # cannot create extra columns on normally resolved tables.
    if len(local_xs) < 3 or len(local_ys) < 3:
        low_edges = cv2.Canny(gray, 10, 40)
        if len(local_xs) < 3:
            local_xs = group_indices(np.flatnonzero(low_edges.sum(axis=0) >= max(80, int(gray.shape[0] * 0.24)) * 255))
        if len(local_ys) < 3:
            local_ys = group_indices(np.flatnonzero(low_edges.sum(axis=1) >= max(80, int(gray.shape[1] * 0.30)) * 255))
    xs = [x + search_x1 for x in local_xs]
    ys = [y + search_y1 for y in local_ys]

    header_band = [
        token
        for token in tokens
        if abs(center(token["box"])[1] - header_center_y) <= 22
        and candidate_rect["x"] - 30 <= center(token["box"])[0] <= candidate_rect["x"] + candidate_rect["width"] + 30
    ]
    # The first-cell label can have been inferred from an adjacent 全队 token.  Its center is
    # still a valid left-boundary cue; all surviving header tokens establish the right side.
    header_centers = [header_center_x] + [center(token["box"])[0] for token in header_band]
    left = nearest_before(xs, min(header_centers) - 8)
    right = nearest_after(xs, max(header_centers) + 8)
    top = nearest_before(ys, header_center_y + 4)
    if left is None or right is None or top is None:
        return {
            "status": "grid_not_resolved",
            "reason": "missing long vertical or horizontal source-grid lines",
            "search_rect": rect(search_x1, search_y1, search_x2 - search_x1, search_y2 - search_y1),
            "vertical_lines": xs,
            "horizontal_lines": ys,
        }

    verticals = [line for line in xs if left <= line <= right]
    horizontals = contiguous_from_start(ys, top)
    if len(verticals) < 3 or len(horizontals) < 3:
        # Retry selection with projection peaks.  This is intentionally delayed until the
        # regular morphology result has proved insufficient for this particular table.
        low_edges = cv2.Canny(gray, 10, 40)
        xs = group_indices(np.flatnonzero(low_edges.sum(axis=0) >= max(80, int(gray.shape[0] * 0.24)) * 255))
        ys = group_indices(np.flatnonzero(low_edges.sum(axis=1) >= max(80, int(gray.shape[1] * 0.30)) * 255))
        xs = [x + search_x1 for x in xs]
        ys = [y + search_y1 for y in ys]
        left = nearest_before(xs, min(header_centers) - 8)
        right = nearest_after(xs, max(header_centers) + 8)
        top = nearest_before(ys, header_center_y + 4)
        if left is not None and right is not None and top is not None:
            verticals = [line for line in xs if left <= line <= right]
            horizontals = contiguous_from_start(ys, top)
    if len(verticals) < 3 or len(horizontals) < 3:
        return {
            "status": "grid_not_resolved",
            "reason": "too few contiguous grid boundaries",
            "search_rect": rect(search_x1, search_y1, search_x2 - search_x1, search_y2 - search_y1),
            "vertical_lines": verticals,
            "horizontal_lines": horizontals,
        }
    return {
        "status": "grid_resolved",
        "search_rect": rect(search_x1, search_y1, search_x2 - search_x1, search_y2 - search_y1),
        "source_rect": rect(left, top, right - left, horizontals[-1] - top),
        "vertical_lines": verticals,
        "horizontal_lines": horizontals,
    }


def crop(image: np.ndarray, area: dict[str, int], inset: int = 3) -> np.ndarray:
    x1 = area["x"] + inset
    y1 = area["y"] + inset
    x2 = area["x"] + area["width"] - inset
    y2 = area["y"] + area["height"] - inset
    return image[max(0, y1):max(y1, y2), max(0, x1):max(x1, x2)]


def recognize_cell(engine: RapidOCR | None, image: np.ndarray, area: dict[str, int]) -> dict[str, Any]:
    if engine is None:
        return {"text": None, "confidence": None, "engine": "not_requested_geometry_and_raw_tokens_only"}
    cell = crop(image, area)
    if cell.size == 0:
        return {"text": None, "confidence": None, "engine": "rapidocr_recognition_only", "reason": "empty_crop"}
    result = engine(cell, use_det=False)
    texts = list(result.txts or [])
    scores = list(result.scores or [])
    text = str(texts[0]).strip() if texts else None
    confidence = round(float(scores[0]), 6) if scores else None
    return {"text": text or None, "confidence": confidence, "engine": "rapidocr_recognition_only"}


def cell_record(
    engine: RapidOCR | None,
    image: np.ndarray,
    area: dict[str, int],
    tokens: list[dict[str, Any]],
) -> dict[str, Any]:
    raw_tokens = tokens_in_rect(tokens, area)
    raw_tokens.sort(key=lambda token: (center(token["box"])[1], center(token["box"])[0]))
    return {
        "source_rect": area,
        "raw": {
            "text": " ".join(token["text"] for token in raw_tokens) or None,
            "tokens": raw_tokens,
        },
        "proposal": recognize_cell(engine, image, area),
        "review_status": "unreviewed",
    }


def build_table(
    engine: RapidOCR | None,
    image: np.ndarray,
    table: dict[str, Any],
    panel: dict[str, Any],
    tokens: list[dict[str, Any]],
) -> dict[str, Any]:
    geometry = refine_grid(image, table, panel["source_rect"], tokens)
    output: dict[str, Any] = {
        "id": f"{panel['id']}:{table['id']}",
        "panel_id": panel["id"],
        "candidate_source_rect": table["source_rect"],
        "header_anchor_raw": table["header_token"],
        "geometry": geometry,
        "metadata_candidates": {
            "configuration_name": panel["metadata"]["configuration_name"],
            "configuration_name_raw_candidates": panel["metadata"]["configuration_name_raw_candidates"],
            "difficulty": panel["difficulty"],
            "stability": panel["stability"],
            "semantic_status": "copied_unreviewed_from_documentary_recognition",
        },
        "review_status": "unreviewed_not_optimizer_input",
    }
    if geometry["status"] != "grid_resolved":
        output["headers"] = []
        output["rows"] = []
        return output

    verticals = geometry["vertical_lines"]
    horizontals = geometry["horizontal_lines"]
    columns = [rect(verticals[index], horizontals[0], verticals[index + 1] - verticals[index], horizontals[-1] - horizontals[0]) for index in range(len(verticals) - 1)]
    output["columns"] = [{"index": index, "source_rect": column} for index, column in enumerate(columns)]
    output["headers"] = [
        cell_record(engine, image, rect(verticals[index], horizontals[0], verticals[index + 1] - verticals[index], horizontals[1] - horizontals[0]), tokens)
        for index in range(len(verticals) - 1)
    ]
    rows = []
    for row_index in range(1, len(horizontals) - 1):
        row_rect = rect(verticals[0], horizontals[row_index], verticals[-1] - verticals[0], horizontals[row_index + 1] - horizontals[row_index])
        rows.append(
            {
                "index": row_index - 1,
                "source_rect": row_rect,
                "cells": [
                    cell_record(
                        engine,
                        image,
                        rect(verticals[column_index], horizontals[row_index], verticals[column_index + 1] - verticals[column_index], horizontals[row_index + 1] - horizontals[row_index]),
                        tokens,
                    )
                    for column_index in range(len(verticals) - 1)
                ],
            }
        )
    output["rows"] = rows
    return output


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--recognition", type=Path, default=DEFAULT_RECOGNITION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=None, help="process only the first N tables for a quick diagnostic")
    parser.add_argument("--ocr-cells", action="store_true", help="also run RapidOCR recognition-only for every resolved cell (slow; off by default)")
    args = parser.parse_args()
    started = time.monotonic()
    recognition = load_json(args.recognition)
    image = cv2.imread(str(args.source), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(args.source)
    all_tables = [(panel, table) for panel in recognition["panels"] for table in panel.get("tables", [])]
    if args.limit is not None:
        all_tables = all_tables[:args.limit]
    phase = "measuring source grids and associating existing raw OCR tokens"
    if args.ocr_cells:
        phase += "; running optional per-cell OCR"
    log(f"Loaded {len(all_tables)} candidate tables; {phase}")
    engine = None
    if args.ocr_cells:
        from rapidocr import RapidOCR

        engine = RapidOCR()
    output_tables = []
    for index, (panel, table) in enumerate(all_tables, start=1):
        output_tables.append(build_table(engine, image, table, panel, recognition["raw_ocr"]["tokens"]))
        if index == 1 or index % 10 == 0 or index == len(all_tables):
            resolved = sum(item["geometry"]["status"] == "grid_resolved" for item in output_tables)
            log(f"Table {index}/{len(all_tables)}; resolved grids={resolved}")
    payload = {
        "schema_version": "dps-cell-proposals-draft-1",
        "status": "unreviewed_documentary_candidate_not_optimizer_input",
        "source": {
            "reference_image": str(args.source.relative_to(ROOT)).replace("\\", "/"),
            "recognition_input": str(args.recognition.relative_to(ROOT)).replace("\\", "/"),
            "dimensions": {"width": int(image.shape[1]), "height": int(image.shape[0])},
        },
        "method": {
            "grid_geometry": "Canny plus long horizontal/vertical morphology on source pixels",
            "cell_text": "existing raw OCR tokens; RapidOCR recognition-only runs only when --ocr-cells is supplied",
            "unit_semantics": "not inferred; raw and proposal strings retain source spelling and punctuation",
        },
        "summary": {
            "candidate_table_count": len(all_tables),
            "grid_resolved_count": sum(item["geometry"]["status"] == "grid_resolved" for item in output_tables),
            "grid_unresolved_count": sum(item["geometry"]["status"] != "grid_resolved" for item in output_tables),
        },
        "tables": output_tables,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Wrote {args.output.relative_to(ROOT)} in {time.monotonic() - started:.1f}s; {payload['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
