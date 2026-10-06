#!/usr/bin/env python3
"""Offline, conservative transcription of the version 3.5.2 DPS reference image.

The script intentionally produces a review dataset, not optimizer input.  OCR strings and
coordinates are kept exactly as returned by RapidOCR; fields that cannot be verified stay
null.  It never queries remote services or uses player/account screenshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image
from rapidocr import RapidOCR


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data/public/reference-dps.jpg"
DEFAULT_TEMPLATE = ROOT / "data/templates/dps-table.json"
DEFAULT_ATLAS = ROOT / "data/portrait-atlas.json"
DEFAULT_OUTPUT = ROOT / "data/dps-recognition-v3.5.2.json"
DEFAULT_DELIVERY_JSON = ROOT / "outputs/dps-recognition-v3.5.2.json"
DEFAULT_REPORT = ROOT / "outputs/dps-recognition-v3.5.2.md"

TILE_WIDTH = 1100
TILE_HEIGHT = 900
OVERLAP = 180
OCR_SCORE = 0.35
AVATAR_SIZE = 98
AVATAR_MIN_SCORE = 0.56
AVATAR_MIN_MARGIN = 0.075
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:%|万|w|W|k|K)?$")
DIFFICULTY_RE = re.compile(r"^(?:低|中低|中|中高|高)$")


def log(message: str) -> None:
    print(message, flush=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rect_dict(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}


def overlap_starts(limit: int, size: int, overlap: int) -> list[int]:
    step = size - overlap
    starts = list(range(0, max(limit - size, 0) + 1, step))
    final = max(0, limit - size)
    if not starts or starts[-1] != final:
        starts.append(final)
    return starts


def normalize_box(box: Any, dx: int, dy: int) -> list[list[int]]:
    points = np.asarray(box).reshape(-1, 2)
    return [[int(round(x + dx)), int(round(y + dy))] for x, y in points]


def bounds(box: list[list[int]]) -> tuple[int, int, int, int]:
    arr = np.asarray(box)
    x1, y1 = arr.min(axis=0)
    x2, y2 = arr.max(axis=0)
    return int(x1), int(y1), int(x2), int(y2)


def iou(a: list[list[int]], b: list[list[int]]) -> float:
    ax1, ay1, ax2, ay2 = bounds(a)
    bx1, by1, bx2, by2 = bounds(b)
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = max(1, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
    return inter / union


def same_token(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a["text"] != b["text"]:
        return False
    if iou(a["box"], b["box"]) >= 0.55:
        return True
    ax1, ay1, ax2, ay2 = bounds(a["box"])
    bx1, by1, bx2, by2 = bounds(b["box"])
    return abs((ax1 + ax2) - (bx1 + bx2)) <= 12 and abs((ay1 + ay2) - (by1 + by2)) <= 12


def run_tiled_ocr(image: np.ndarray) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    """OCR every part of the source, retaining only one copy of overlap duplicates."""
    height, width = image.shape[:2]
    engine = RapidOCR()
    tokens: list[dict[str, Any]] = []
    tiles: list[dict[str, int]] = []
    starts_y = overlap_starts(height, TILE_HEIGHT, OVERLAP)
    starts_x = overlap_starts(width, TILE_WIDTH, OVERLAP)
    total = len(starts_y) * len(starts_x)
    index = 0
    for y in starts_y:
        for x in starts_x:
            index += 1
            crop = image[y : min(y + TILE_HEIGHT, height), x : min(x + TILE_WIDTH, width)]
            result = engine(crop, text_score=OCR_SCORE)
            boxes = result.boxes if result.boxes is not None else []
            texts = result.txts if result.txts is not None else []
            scores = result.scores if result.scores is not None else []
            for box, text, score in zip(boxes, texts, scores):
                text = str(text).strip()
                if not text:
                    continue
                token = {
                    "text": text,
                    "box": normalize_box(box, x, y),
                    "confidence": round(float(score), 6),
                    "tile": rect_dict(x, y, crop.shape[1], crop.shape[0]),
                }
                duplicate = next((old for old in reversed(tokens) if same_token(old, token)), None)
                if duplicate is None:
                    tokens.append(token)
                elif token["confidence"] > duplicate["confidence"]:
                    duplicate.update(token)
            tiles.append(rect_dict(x, y, crop.shape[1], crop.shape[0]))
            if index == 1 or index % 10 == 0 or index == total:
                log(f"OCR tile {index}/{total}; retained tokens={len(tokens)}")
    tokens.sort(key=lambda token: (bounds(token["box"])[1], bounds(token["box"])[0], token["text"]))
    for token in tokens:
        token.pop("tile", None)
    return tokens, tiles


def panel_candidates(section: dict[str, Any], image: np.ndarray) -> list[dict[str, Any]]:
    """Find table/grid rectangles in one anchored version band.

    A panel has long black vertical and horizontal grid strokes.  The thresholds are applied
    only inside known section bands, avoiding the decorative full-height page artwork.
    """
    start, end = section["content_y_px"]
    crop = image[start : end + 1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    dark = (gray < 80).astype(np.uint8) * 255
    vertical = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 70)))
    horizontal = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (70, 1)))
    lines = cv2.dilate(cv2.bitwise_or(vertical, horizontal), cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
    contours, _ = cv2.findContours(lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        # A real card/table is significantly wider and taller than a text underline.
        if width < 260 or height < 350 or width / max(height, 1) > 2.4:
            continue
        if x < 30 or x + width > image.shape[1] - 30:
            continue
        found.append((x, y + start, width, height))
    found.sort(key=lambda rect: (rect[1], rect[0]))
    # Morphology can return overlapping replacements.  Retain the largest version only when
    # they nearly describe the same exterior rectangle.
    kept: list[tuple[int, int, int, int]] = []
    for candidate in found:
        cx, cy, cw, ch = candidate
        replacement = None
        for old_i, old in enumerate(kept):
            ox, oy, ow, oh = old
            inter = max(0, min(cx + cw, ox + ow) - max(cx, ox)) * max(0, min(cy + ch, oy + oh) - max(cy, oy))
            if inter / max(1, min(cw * ch, ow * oh)) > 0.9:
                replacement = old_i
                break
        if replacement is None:
            kept.append(candidate)
        elif candidate[2] * candidate[3] > kept[replacement][2] * kept[replacement][3]:
            kept[replacement] = candidate
    return [
        {
            "id": f"{section['id']}-panel-{index + 1:02d}",
            "section_id": section["id"],
            "source_rect": rect_dict(*rect),
            "detection": {
                "method": "dark_grid_morphology_in_template_section",
                "status": "review_required",
                "confidence": round(min(0.96, 0.55 + min(rect[2] * rect[3], 1_200_000) / 3_000_000), 3),
            },
        }
        for index, rect in enumerate(kept)
    ]


def point_in_rect(point: tuple[float, float], rect: dict[str, int], pad: int = 0) -> bool:
    x, y = point
    return rect["x"] - pad <= x <= rect["x"] + rect["width"] + pad and rect["y"] - pad <= y <= rect["y"] + rect["height"] + pad


def token_center(token: dict[str, Any]) -> tuple[float, float]:
    x1, y1, x2, y2 = bounds(token["box"])
    return (x1 + x2) / 2, (y1 + y2) / 2


def tokens_for_rect(tokens: Iterable[dict[str, Any]], rect: dict[str, int]) -> list[dict[str, Any]]:
    return [token for token in tokens if point_in_rect(token_center(token), rect, pad=2)]


def parsed_cell(text: str) -> dict[str, Any]:
    value: float | None = None
    normalized = text.replace("，", ".").replace("％", "%").strip()
    if NUMBER_RE.match(normalized):
        numeric = normalized.rstrip("%万wWkK")
        try:
            value = float(numeric)
        except ValueError:
            value = None
    return {"raw": text, "number": value}


def cluster_rows(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tokens:
        return []
    ordered = sorted(tokens, key=lambda token: (token_center(token)[1], token_center(token)[0]))
    rows: list[list[dict[str, Any]]] = []
    centers: list[float] = []
    for token in ordered:
        _, y = token_center(token)
        height = max(8, bounds(token["box"])[3] - bounds(token["box"])[1])
        target = next((i for i, center in enumerate(centers) if abs(y - center) <= max(11, height * 0.7)), None)
        if target is None:
            rows.append([token])
            centers.append(y)
        else:
            rows[target].append(token)
            centers[target] = sum(token_center(item)[1] for item in rows[target]) / len(rows[target])
    output = []
    for row in rows:
        row.sort(key=lambda token: token_center(token)[0])
        y = int(round(sum(token_center(token)[1] for token in row) / len(row)))
        output.append(
            {
                "y": y,
                "raw_text": " | ".join(token["text"] for token in row),
                "cells": [parsed_cell(token["text"]) | {"box": token["box"], "confidence": token["confidence"]} for token in row],
            }
        )
    return output


def feature(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    image = cv2.resize(image, (72, 72), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    gray = lab[:, :, 0].astype(np.float32)
    gray = (gray - gray.mean()) / (gray.std() + 1e-6)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [12, 8], [0, 180, 0, 256])
    histogram = cv2.normalize(histogram, None).astype(np.float32)
    # Low-resolution layout is more robust than individual pixels after UI scaling.
    layout = cv2.resize(lab, (18, 18), interpolation=cv2.INTER_AREA).astype(np.float32)
    layout = (layout - layout.mean(axis=(0, 1), keepdims=True)) / (layout.std(axis=(0, 1), keepdims=True) + 1e-6)
    return gray, histogram, layout


def make_portrait_features(atlas: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    sift = cv2.SIFT_create()
    for item in atlas["portraits"]:
        image_path = ROOT / item["image"]
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"Atlas image missing: {image_path}")
        gray, histogram, layout = feature(image)
        _, descriptors = sift.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
        records.append({
            "item": item,
            "gray": gray,
            "histogram": histogram,
            "layout": layout,
            "sift_descriptors": descriptors,
        })
    return records


def match_avatar(crop: np.ndarray, atlas_features: list[dict[str, Any]]) -> dict[str, Any]:
    gray, histogram, layout = feature(crop)
    sift = cv2.SIFT_create()
    _, source_descriptors = sift.detectAndCompute(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), None)
    matcher = cv2.BFMatcher()
    candidates = []
    for record in atlas_features:
        gray_corr = float(np.mean(gray * record["gray"]))
        color_corr = float(cv2.compareHist(histogram, record["histogram"], cv2.HISTCMP_CORREL))
        layout_corr = float(np.mean(layout * record["layout"]))
        base_score = max(0.0, min(1.0, 0.5 + 0.20 * gray_corr + 0.16 * color_corr + 0.14 * layout_corr))
        good_matches = 0
        if source_descriptors is not None and record["sift_descriptors"] is not None:
            pairs = matcher.knnMatch(source_descriptors, record["sift_descriptors"], k=2)
            good_matches = sum(1 for pair in pairs if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance)
        # Repeated UI icons are scaled/cropped differently in the source.  Feature matches
        # therefore supply the identity evidence; colour/layout is a low-weight tie breaker.
        sift_score = min(1.0, good_matches / 10.0)
        score = 0.30 * base_score + 0.70 * sift_score
        candidates.append({
            "name": record["item"]["canonical"],
            "id": record["item"]["id"],
            "score": round(score, 6),
            "feature_matches": good_matches,
        })
    candidates.sort(key=lambda item: (-item["score"], item["name"]))
    top = candidates[0]
    margin = top["score"] - candidates[1]["score"] if len(candidates) > 1 else top["score"]
    unknown = top["score"] < AVATAR_MIN_SCORE or margin < AVATAR_MIN_MARGIN
    return {
        "name": None if unknown else top["name"],
        "candidates": candidates[:5],
        "score": top["score"],
        "margin": round(margin, 6),
        "unknown": unknown,
        "match_method": "SIFT_ratio_matches_with_normalized_lab_gray_hsv_layout_tiebreaker",
    }


def extract_avatars(image: np.ndarray, rect: dict[str, int], atlas_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    # Detected exterior includes the three portrait cells at the left. Their 1:1 portrait
    # windows are fixed by the table visual grammar, not by any character label.
    for index in range(3):
        x = rect["x"] + 2
        y = rect["y"] + 5 + index * 100
        crop = image[y : y + AVATAR_SIZE, x : x + AVATAR_SIZE]
        item = match_avatar(crop, atlas_features) if crop.shape[:2] == (AVATAR_SIZE, AVATAR_SIZE) else {
            "name": None, "candidates": [], "score": 0.0, "margin": 0.0, "unknown": True, "match_method": "out_of_bounds"
        }
        item["source_rect"] = rect_dict(x, y, crop.shape[1], crop.shape[0])
        item["slot"] = index + 1
        records.append(item)
    return records


def panel_metadata(panel_tokens: list[dict[str, Any]], panel_rect: dict[str, int]) -> dict[str, Any]:
    # These values are deliberately conservative: OCR can preserve candidate header strings,
    # but tags only become semantic fields if an exact canonical value is isolated.
    header_cutoff = panel_rect["y"] + min(170, max(75, panel_rect["height"] // 5))
    header = [token for token in panel_tokens if token_center(token)[1] <= header_cutoff]
    exact_tags = [token["text"] for token in header if DIFFICULTY_RE.match(token["text"])]
    difficulty = exact_tags[0] if exact_tags else None
    stability = exact_tags[1] if len(exact_tags) > 1 else None
    return {
        "configuration_name": None,
        "configuration_name_raw_candidates": [token["text"] for token in header[:8]],
        "difficulty": difficulty,
        "stability": stability,
        "semantic_status": "unverified_ocr_only",
    }


def detect_table_regions(panel_tokens: list[dict[str, Any]], panel_rect: dict[str, int]) -> list[dict[str, Any]]:
    """Split a wide detected exterior into its OCR-anchored table regions.

    The 3.0 lower left configuration has three adjacent tables inside one common outer card.
    Treating its whole exterior as one table would mix cells from three visual tables.  Header
    labels are OCR evidence only; the computed bounds are explicitly review-required.
    """
    anchors = [token for token in panel_tokens if token["text"] in {"配置", "链子"}]
    # OCR occasionally omits only the first cell ("配置") while retaining adjacent "全队".
    # Recover an explicitly marked geometry anchor instead of dropping that visual table.
    for token in panel_tokens:
        if token["text"] != "全队":
            continue
        tx, ty = token_center(token)
        has_preceding_anchor = any(
            abs(token_center(anchor)[1] - ty) <= 18 and 45 <= tx - token_center(anchor)[0] <= 145
            for anchor in anchors
        )
        if has_preceding_anchor:
            continue
        inferred = dict(token)
        inferred["text"] = "[inferred_from_全队]"
        inferred["box"] = [[x - 95, y] for x, y in token["box"]]
        anchors.append(inferred)
    anchors.sort(key=lambda token: (token_center(token)[1], token_center(token)[0]))
    bands: list[list[dict[str, Any]]] = []
    for token in anchors:
        _, y = token_center(token)
        current = next((band for band in bands if abs(y - token_center(band[0])[1]) <= 18), None)
        if current is None:
            bands.append([token])
        else:
            current.append(token)
    # A header row with multiple first-column labels is strongest.  Narrow cards still retain
    # their single anchor.  Use only the earliest likely header row for this panel.
    candidate_bands = [band for band in bands if panel_rect["y"] + 45 <= token_center(band[0])[1] <= panel_rect["y"] + min(300, panel_rect["height"] // 3)]
    if not candidate_bands:
        return []
    anchors = sorted(max(candidate_bands, key=lambda band: (len(band), -token_center(band[0])[1])), key=lambda token: token_center(token)[0])
    regions = []
    for index, anchor in enumerate(anchors):
        ax1, ay1, ax2, ay2 = bounds(anchor["box"])
        next_x = bounds(anchors[index + 1]["box"])[0] if index + 1 < len(anchors) else panel_rect["x"] + panel_rect["width"] - 14
        left = max(panel_rect["x"], ax1 - 45)
        right = min(panel_rect["x"] + panel_rect["width"], max(left + 150, next_x - 16))
        top = max(panel_rect["y"], ay1 - 24)
        # Tables finish above the lower note / source box.  A fixed upper bound only supplies
        # a review crop; raw_ocr remains the complete, authoritative transcription.
        bottom = min(panel_rect["y"] + panel_rect["height"], top + 720)
        table_rect = rect_dict(left, top, right - left, bottom - top)
        table_tokens = tokens_for_rect(panel_tokens, table_rect)
        regions.append({
            "id": f"table-{index + 1}",
            "source_rect": table_rect,
            "detection": "OCR_header_anchored_review_required",
            "header_token": anchor,
            "table_rows": cluster_rows(table_tokens),
        })
    return regions


def digest_panel(panel: dict[str, Any], tokens: list[dict[str, Any]], image: np.ndarray, atlas_features: list[dict[str, Any]]) -> dict[str, Any]:
    rect = panel["source_rect"]
    panel_tokens = tokens_for_rect(tokens, rect)
    # Rows include every OCR block inside the panel.  Downstream consumers must use raw_text
    # and boxes for review; no row is promoted to an optimizer configuration here.
    panel["characters"] = extract_avatars(image, rect, atlas_features)
    panel["metadata"] = panel_metadata(panel_tokens, rect)
    panel["difficulty"] = panel["metadata"]["difficulty"]
    panel["stability"] = panel["metadata"]["stability"]
    panel["table_rows"] = cluster_rows(panel_tokens)
    panel["tables"] = detect_table_regions(panel_tokens, rect)
    panel["detection"]["table_region_count"] = len(panel["tables"])
    if rect["width"] >= 800:
        panel["detection"]["wide_multi_table_group"] = True
    panel["notes"] = [token for token in panel_tokens if token_center(token)[1] >= rect["y"] + rect["height"] * 0.76]
    panel["ocr_blocks"] = panel_tokens
    panel["optimizer_eligible"] = False
    panel["optimizer_exclusion_reason"] = "configuration, weapon, difficulty and numeric cells require human verification"
    return panel


def output_payload(
    source: Path,
    template: dict[str, Any],
    tokens: list[dict[str, Any]],
    tiles: list[dict[str, int]],
    panels: list[dict[str, Any]],
) -> dict[str, Any]:
    image = Image.open(source)
    unknown = sum(character["unknown"] for panel in panels for character in panel["characters"])
    summary = {
        "source_dimensions": {"width": image.width, "height": image.height},
        "ocr_tile_count": len(tiles),
        "ocr_token_count": len(tokens),
        "panel_count": len(panels),
        "table_count": sum(len(panel.get('tables', [])) for panel in panels),
        "avatar_slots": len(panels) * 3,
        "unknown_avatar_slots": unknown,
        "optimizer_eligible_panel_count": 0,
    }
    return {
        "schema_version": "dps-recognition-draft-1",
        "recognition_status": "documentary_draft_requires_human_review",
        "source": {
            "path": str(source.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(source),
            "width": image.width,
            "height": image.height,
            "observed_version": template["source"]["observed_version"],
        },
        "recognition": {
            "engine": "rapidocr.RapidOCR (offline onnxruntime)",
            "tile_size": {"width": TILE_WIDTH, "height": TILE_HEIGHT, "overlap": OVERLAP},
            "text_score_threshold": OCR_SCORE,
            "panel_detection": "dark grid morphology constrained to data/templates/dps-table.json section bands",
            "avatar_matching": "provided data/portrait-atlas.json only; scores are similarities, not identity probabilities",
        },
        "template": {
            "template_id": template["template_id"],
            "section_bands": template["section_bands"],
            "warnings": template["warnings"],
        },
        "summary": summary,
        "panels": panels,
        "raw_ocr": {
            "tiles": tiles,
            "tokens": tokens,
            "reading_order_text": "\n".join(token["text"] for token in tokens),
        },
        "limitations": [
            "OCR text, numeric cells, header tags and avatar matches are evidence for review, not verified DPS data.",
            "No panel is emitted as an optimizer record; missing or unclear configuration and weapon fields remain null.",
            "Avatar names are null whenever the supplied-atlas similarity or separation margin is insufficient.",
        ],
    }


def write_report(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    counts = Counter(panel["section_id"] for panel in payload["panels"])
    examples = []
    for panel in payload["panels"][:3]:
        names = ", ".join(character["name"] or "unknown" for character in panel["characters"])
        examples.append(f"- `{panel['id']}`: {names}; rect `{panel['source_rect']}`")
    report = "\n".join(
        [
            "# DPS recognition draft v3.5.2",
            "",
            "This is an offline, documentary OCR result. It does not update demo data or optimizer inputs.",
            "",
            f"- Source: `{payload['source']['path']}` ({payload['source']['width']}×{payload['source']['height']})",
            f"- OCR: {summary['ocr_token_count']} deduplicated tokens from {summary['ocr_tile_count']} overlapping tiles.",
            f"- Detected panels: {summary['panel_count']} ({', '.join(f'{key}: {value}' for key, value in sorted(counts.items()))}).",
            f"- Avatar slots: {summary['avatar_slots']}; marked unknown: {summary['unknown_avatar_slots']}.",
            f"- Visually checked avatar slots: {summary.get('manually_checked_avatar_slots', 0)}.",
            f"- Full visually transcribed tables: {summary.get('visually_transcribed_table_count', 0)}; rows: {summary.get('visually_transcribed_row_count', 0)}.",
            "- Optimizer-eligible panels: 0. Every row remains review-only.",
            "",
            "## Reproduce",
            "",
            "```powershell",
            ".venv\\Scripts\\python.exe scripts\\reparse_dps.py",
            "```",
            "",
            "The JSON preserves all OCR strings, quadrilateral boxes and confidence values in `raw_ocr.tokens`; panel-level copies make table review easier. Avatar candidates only compare against `data/portrait-atlas.json` and remain `null` when their score/margin is weak.",
            "",
            "## First detected panels",
            "",
            *examples,
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--atlas", type=Path, default=DEFAULT_ATLAS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delivery-json", type=Path, default=DEFAULT_DELIVERY_JSON)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--skip-review", action="store_true", help="Skip source-specific manual annotations for a different reference image.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    source = args.source.resolve()
    template = load_json(args.template.resolve())
    atlas = load_json(args.atlas.resolve())
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(source)
    log(f"Loaded {source.name}: {image.shape[1]}x{image.shape[0]}; atlas portraits={len(atlas['portraits'])}")
    tokens, tiles = run_tiled_ocr(image)
    panels = [panel for section in template["section_bands"] if section["id"] != "intro" for panel in panel_candidates(section, image)]
    log(f"Detected {len(panels)} panel candidates; matching three supplied-atlas avatar slots per panel")
    atlas_features = make_portrait_features(atlas)
    panels = [digest_panel(panel, tokens, image, atlas_features) for panel in panels]
    payload = output_payload(source, template, tokens, tiles, panels)
    review_path = ROOT / 'data/dps-review-v3.5.2.json'
    if review_path.is_file() and not args.skip_review:
        from apply_dps_reviews import apply_reviews
        payload = apply_reviews(payload, load_json(review_path))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.write_text(serialized, encoding="utf-8")
    args.delivery_json.parent.mkdir(parents=True, exist_ok=True)
    args.delivery_json.write_text(serialized, encoding="utf-8")
    write_report(payload, args.report)
    elapsed = time.monotonic() - started
    log(f"Wrote {args.output.relative_to(ROOT)}, {args.delivery_json.relative_to(ROOT)} and {args.report.relative_to(ROOT)} in {elapsed:.1f}s; {payload['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
