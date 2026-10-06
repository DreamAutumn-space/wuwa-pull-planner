"""Propose only high-confidence same-source avatar matches for DPS review.

This deliberately does not modify recognition output.  It treats manual checks and
strong existing atlas/SIFT matches as labelled source patches, then looks for an
almost pixel-identical avatar among the currently unknown slots.  The resulting
JSON is a review aid for the parent workflow.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
AVATAR_SIZE = 98
SCALES = (0.94, 0.96, 0.98, 1.00, 1.02, 1.04, 1.06)
NCC_MIN = 0.94
NCC_MARGIN_MIN = 0.04
FUTURE_LOCKED_NAMES = {"清宵", "景", "燃心"}


def rect_dict(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}


def crop(image: np.ndarray, rect: dict[str, int]) -> np.ndarray | None:
    x, y, width, height = (rect[key] for key in ("x", "y", "width", "height"))
    if x < 0 or y < 0 or x + width > image.shape[1] or y + height > image.shape[0]:
        return None
    patch = image[y : y + height, x : x + width]
    return patch if patch.size else None


def name_is_allowed(name: str) -> bool:
    # Do not make a face-only proposal for Rover forms, and 3.5.2 has no valid
    # role mapping for the later character set.
    return name not in FUTURE_LOCKED_NAMES and "漂泊者" not in name


def is_strict_atlas_match(character: dict[str, Any], atlas_names: set[str]) -> bool:
    candidates = character.get("candidates") or []
    top = candidates[0] if candidates else {}
    return (
        character.get("name") in atlas_names
        and not character.get("unknown", character.get("name") is None)
        and character.get("score", 0.0) >= 0.88
        and character.get("margin", 0.0) > 0.10
        and top.get("name") == character.get("name")
        and top.get("feature_matches", 0) >= 12
    )


def source_kind(character: dict[str, Any], atlas_names: set[str]) -> str | None:
    name = character.get("name")
    if not isinstance(name, str) or name not in atlas_names or not name_is_allowed(name):
        return None
    if character.get("identity_verification"):
        return "manual"
    if is_strict_atlas_match(character, atlas_names):
        return "strict_sift"
    return None


def has_avatar_content(patch: np.ndarray) -> bool:
    """Reject empty/white table cells before pixel similarity can mislead us."""
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    central = gray[16:-16, 16:-16]
    return float(central.std()) >= 14.0 and float(np.mean(central < 235)) >= 0.18


def ncc_with_small_geometry(source: np.ndarray, target: np.ndarray) -> tuple[float, dict[str, int]]:
    """Find raw-pixel NCC over only small scale and translation variation."""
    pad = 8
    padded = cv2.copyMakeBorder(target, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    best_score = -1.0
    best_rect: dict[str, int] | None = None
    for scale in SCALES:
        width = max(12, round(source.shape[1] * scale))
        height = max(12, round(source.shape[0] * scale))
        template = cv2.resize(source, (width, height), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        scores = cv2.matchTemplate(padded, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        if score > best_score:
            best_score = float(score)
            best_rect = rect_dict(point[0] - pad, point[1] - pad, width, height)
    assert best_rect is not None
    # Geometry must remain an avatar-to-avatar comparison, never a search across
    # table text or white space outside the declared target ROI.
    if abs(best_rect["x"]) > 5 or abs(best_rect["y"]) > 5:
        return -1.0, best_rect
    return best_score, best_rect


def make_montage(source: np.ndarray, target: np.ndarray, path: Path, label: str) -> None:
    scale = 3
    left = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    right = cv2.resize(target, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    canvas = np.full((left.shape[0] + 42, left.shape[1] + right.shape[1] + 12, 3), 255, dtype=np.uint8)
    canvas[42:, :left.shape[1]] = left
    canvas[42:, left.shape[1] + 12:] = right
    cv2.putText(canvas, label, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), canvas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "dps-recognition-v3.5.2.json")
    parser.add_argument("--image", type=Path, default=ROOT / "data" / "public" / "reference-dps.jpg")
    parser.add_argument("--atlas", type=Path, default=ROOT / "data" / "portrait-atlas.json")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "work" / "dps-avatar-refinement")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    atlas = json.loads(args.atlas.read_text(encoding="utf-8"))
    atlas_names = {item["canonical"] for item in atlas["portraits"]}
    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not read image: {args.image}")

    trusted: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for panel in payload["panels"]:
        for character in panel["characters"]:
            row = {"panel_id": panel["id"], "slot": character["slot"], "character": character}
            if character.get("unknown", character.get("name") is None):
                if not character.get("identity_verification"):
                    targets.append(row)
                continue
            kind = source_kind(character, atlas_names)
            if kind:
                row["kind"] = kind
                trusted.append(row)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    qa_dir = args.work_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    trusted_patches: list[dict[str, Any]] = []
    for row in trusted:
        patch = crop(image, row["character"]["source_rect"])
        if patch is not None and patch.shape[:2] == (AVATAR_SIZE, AVATAR_SIZE) and has_avatar_content(patch):
            trusted_patches.append({**row, "patch": patch})

    proposals: list[dict[str, Any]] = []
    best_rejected: list[dict[str, Any]] = []
    for row in targets:
        target = crop(image, row["character"]["source_rect"])
        if target is None or target.shape[:2] != (AVATAR_SIZE, AVATAR_SIZE) or not has_avatar_content(target):
            continue
        best_by_name: dict[str, tuple[float, dict[str, int], dict[str, Any]]] = {}
        for reference in trusted_patches:
            score, geometry = ncc_with_small_geometry(reference["patch"], target)
            name = reference["character"]["name"]
            existing = best_by_name.get(name)
            if existing is None or score > existing[0]:
                best_by_name[name] = (score, geometry, reference)
        ranked = sorted(best_by_name.items(), key=lambda item: (-item[1][0], item[0]))
        if not ranked:
            continue
        name, (ncc, geometry, reference) = ranked[0]
        second = ranked[1][1][0] if len(ranked) > 1 else -1.0
        margin = ncc - second
        if ncc < NCC_MIN or margin < NCC_MARGIN_MIN:
            best_rejected.append({
                "panel_id": row["panel_id"], "slot": row["slot"], "name": name,
                "ncc": ncc, "margin": margin,
                "reference_panel_id": reference["panel_id"], "reference_slot": reference["slot"],
                "source_rect": reference["character"]["source_rect"],
                "target_rect": row["character"]["source_rect"],
            })
            continue
        source_rect = reference["character"]["source_rect"]
        target_rect = row["character"]["source_rect"]
        proposals.append({
            "panel_id": row["panel_id"],
            "slot": row["slot"],
            "name": name,
            "ncc": round(ncc, 6),
            "margin": round(margin, 6),
            "reference_panel_id": reference["panel_id"],
            "reference_slot": reference["slot"],
            "source_rect": source_rect,
            "target_rect": target_rect,
        })

    proposals.sort(key=lambda item: (-item["ncc"], -item["margin"], item["panel_id"], item["slot"]))
    (args.work_dir / "proposals.json").write_text(json.dumps(proposals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for proposal in proposals[:3]:
        source = crop(image, proposal["source_rect"])
        target = crop(image, proposal["target_rect"])
        assert source is not None and target is not None
        safe_panel = proposal["panel_id"].replace("/", "_")
        filename = f"{safe_panel}__slot-{proposal['slot']}__from-{proposal['reference_panel_id']}--{proposal['reference_slot']}.png"
        label = f"{proposal['panel_id']} slot {proposal['slot']} <- {proposal['reference_panel_id']} slot {proposal['reference_slot']}  ncc={proposal['ncc']:.3f}"
        make_montage(source, target, qa_dir / filename, label)

    # When nothing qualifies, preserve one visual audit trail for the strongest
    # rejected result.  Its name makes the rejection explicit and it is not an
    # item in proposals.json.
    if not proposals and best_rejected:
        rejected = sorted(best_rejected, key=lambda item: (-item["ncc"], -item["margin"]))[0]
        source = crop(image, rejected["source_rect"])
        target = crop(image, rejected["target_rect"])
        assert source is not None and target is not None
        filename = f"rejected-ncc-{rejected['ncc']:.3f}__{rejected['panel_id']}__slot-{rejected['slot']}.png"
        label = f"REJECTED ncc={rejected['ncc']:.3f} < {NCC_MIN:.2f}; {rejected['reference_panel_id']} {rejected['reference_slot']} -> {rejected['panel_id']} {rejected['slot']}"
        make_montage(source, target, qa_dir / filename, label)

    print(json.dumps({
        "trusted_sources": len(trusted_patches),
        "targets_examined": len(targets),
        "proposals": len(proposals),
        "qa_montages": min(3, len(proposals)),
        "highest_rejected": sorted(best_rejected, key=lambda item: (-item["ncc"], -item["margin"]))[:3],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
