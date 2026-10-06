import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_template(name: str) -> dict:
    return json.loads((ROOT / "data" / "templates" / name).read_text(encoding="utf-8"))


def test_account_card_calibration_matches_observed_grid():
    template = load_template("account-card.json")
    source = template["source"]
    grid = template["grid"]

    assert (source["width"], source["height"]) == (1000, 2601)
    assert grid["rows"] == 8
    assert grid["columns"] == 5
    assert grid["occupied_slots"] == 37
    assert sum(grid["occupied_by_row"]) == 37
    assert grid["column_left_px"] == sorted(grid["column_left_px"])
    assert grid["row_top_px"] == sorted(grid["row_top_px"])
    for left in grid["column_left_px"]:
        assert 0 <= left < left + grid["card_bbox_px"]["width"] <= source["width"]
    for top in grid["row_top_px"]:
        assert 0 <= top < top + grid["card_bbox_px"]["height"] <= source["height"]
    verify_rectangles(template)


def verify_rectangles(template):
    width, height = template["source"]["width"], template["source"]["height"]
    for rectangle in template["confirmed_rectangles"]:
        pixels = rectangle["pixels"]
        assert 0 <= pixels["x"] < pixels["x"] + pixels["width"] <= width
        assert 0 <= pixels["y"] < pixels["y"] + pixels["height"] <= height
        for key, value in pixels.items():
            divisor = width if key in ("x", "width") else height
            assert math.isclose(rectangle["normalized"][key], value / divisor, abs_tol=1e-8)


def test_dps_calibration_contains_all_observed_section_bands():
    template = load_template("dps-table.json")
    source = template["source"]
    bands = template["section_bands"]
    separators = template["confirmed_rectangles"]

    assert (source["width"], source["height"]) == (3974, 14756)
    assert source["observed_version"] == "V3.5.2"
    assert [band["id"] for band in bands] == [
        "intro",
        "3.0_lower",
        "3.0_upper",
        "2.0_lower",
        "2.0_mid",
        "2.0_upper",
        "1.0",
    ]
    bars = [item for item in separators if item["id"].startswith("separator_")]
    assert len(bars) == 7
    assert all(item["pixels"]["x"] == 0 for item in bars)
    assert all(item["pixels"]["width"] == source["width"] for item in bars)
    assert all(item["confidence"] >= 0.99 for item in bars)
    verify_rectangles(template)
    coverage = []
    for band in bands:
        start, end = band["content_y_px"]
        coverage.append((start, end + 1))
        assert 0 <= start <= end < source["height"]
        for value, normalized in zip((start, end), band["content_y_normalized"]):
            assert math.isclose(normalized, value / source["height"], abs_tol=1e-8)
    coverage.extend((bar["pixels"]["y"], bar["pixels"]["y"] + bar["pixels"]["height"]) for bar in bars)
    coverage.sort()
    assert coverage[0][0] == 0
    assert coverage[-1][1] == source["height"]
    assert all(previous[1] == following[0] for previous, following in zip(coverage, coverage[1:]))
