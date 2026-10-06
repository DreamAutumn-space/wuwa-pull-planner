from __future__ import annotations

import sys
from pathlib import Path

import pytest
cv2 = pytest.importorskip('cv2', reason='Optional offline OCR dependencies are not installed.')
np = pytest.importorskip('numpy')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from extract_dps_cells import group_indices, line_centers, refine_grid, tokens_in_rect  # noqa: E402


def test_group_indices_collapses_two_pixel_grid_strokes() -> None:
    assert group_indices([3, 4, 10, 11, 12, 20]) == [4, 11, 20]
    assert group_indices([20, 21, 24]) == [22]


def test_line_centers_finds_synthetic_grid() -> None:
    image = np.full((180, 240), 255, dtype=np.uint8)
    for x in (20, 80, 150, 220):
        cv2.line(image, (x, 20), (x, 160), 0, 2)
    for y in (20, 55, 90, 125, 160):
        cv2.line(image, (20, y), (220, y), 0, 2)
    xs, ys = line_centers(image)
    assert all(any(abs(found - expected) <= 2 for found in xs) for expected in (20, 80, 150, 220))
    assert all(any(abs(found - expected) <= 2 for found in ys) for expected in (20, 55, 90, 125, 160))


def test_first_reference_table_geometry_and_decimal_percent_tokens() -> None:
    import json

    recognition = json.loads((ROOT / "data/dps-recognition-v3.5.2.json").read_text(encoding="utf-8"))
    panel = next(item for item in recognition["panels"] if item["id"] == "3.0_lower-panel-04")
    image = cv2.imread(str(ROOT / "data/public/reference-dps.jpg"), cv2.IMREAD_COLOR)
    geometry = refine_grid(image, panel["tables"][0], panel["source_rect"], recognition["raw_ocr"]["tokens"])
    assert geometry["status"] == "grid_resolved"
    assert all(abs(found - expected) <= 1 for found, expected in zip(geometry["vertical_lines"][:5], [521, 613, 706, 798, 891]))
    assert all(abs(found - expected) <= 1 for found, expected in zip(geometry["horizontal_lines"][1:4], [2177, 2221, 2264]))
    decimal = tokens_in_rect(recognition["raw_ocr"]["tokens"], {"x": 613, "y": 2177, "width": 93, "height": 44})
    percentage = tokens_in_rect(recognition["raw_ocr"]["tokens"], {"x": 798, "y": 2221, "width": 93, "height": 43})
    assert [token["text"] for token in decimal] == ["13.02"]
    assert [token["text"] for token in percentage] == ["11.53%"]


def test_star_marker_is_preserved_as_raw_string() -> None:
    import json

    recognition = json.loads((ROOT / "data/dps-recognition-v3.5.2.json").read_text(encoding="utf-8"))
    # A marker has no numeric meaning in this extraction layer and must never become zero/null.
    star = tokens_in_rect(recognition["raw_ocr"]["tokens"], {"x": 2390, "y": 10660, "width": 75, "height": 38})
    assert [token["text"] for token in star] == ["***"]
