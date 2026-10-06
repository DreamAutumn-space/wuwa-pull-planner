"""Build the character portrait atlas from four supplied grid screenshots.

The extractor uses lossless source-pixel crops only.  It neither normalizes image
appearance nor reads or writes any account state.  Existing atlas files are never
deleted; supplied sources overwrite only the matching generated portrait paths.

Examples:
    .venv\\Scripts\\python scripts\\extract_portraits.py
    .venv\\Scripts\\python scripts\\extract_portraits.py first.png second.png third.png fourth.png
    .venv\\Scripts\\python scripts\\extract_portraits.py a.png b.png c.png d.png --output-root generated
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = (
    PROJECT_ROOT / "work/portrait-sources/grid-01.png",
    PROJECT_ROOT / "work/portrait-sources/grid-02.png",
    PROJECT_ROOT / "work/portrait-sources/grid-03.png",
    PROJECT_ROOT / "work/portrait-sources/grid-04.png",
)

# Measured regular card grids.  Source 0's selected first card has a separate
# border box below, so that its recorded source bounds remain faithful.
GRIDS = (
    {"xs": (52, 311, 569), "ys": (52, 359, 663, 972, 1276), "width": 226, "height": 282},
    {"xs": (28, 286, 544), "ys": (39, 343, 648, 953, 1257), "width": 226, "height": 279},
    {"xs": (31, 289, 546), "ys": (33, 339, 644, 950, 1254), "width": 225, "height": 279},
    {"xs": (43, 301, 559), "ys": (22, 329, 635, 941), "width": 225, "height": 279},
)

# (ASCII image ID, canonical public name, name aliases).  To extend an atlas for
# a later source layout, update this declaration and GRIDS together.
CHARACTERS = (
    ("xin", "心", ()), ("jingran", "景燃", ()), ("qingxiao", "清宵", ()),
    ("suisui", "穗穗", ()), ("yangyang-xuanling", "秧秧·玄翎", ("秧秧玄翎", "玄翎")), ("luoxela", "洛瑟菈", ()),
    ("luxi", "露西", ()), ("libeika", "丽贝卡", ()), ("daniya", "达妮娅", ()),
    ("feixue", "绯雪", ()), ("xigelika", "西格莉卡", ()), ("lu-hesi", "陆·赫斯", ("陆赫斯",)),
    ("aimisi", "爱弥斯", ()), ("moning", "莫宁", ()), ("linnai", "琳奈", ()),
    ("qianxiao", "千咲", ()), ("qiuyuan", "仇远", ()), ("jiabeilina", "嘉贝莉娜", ()),
    ("younuo", "尤诺", ()), ("aogusita", "奥古斯塔", ()), ("fuluoluo", "弗洛洛", ()),
    ("lupa", "露帕", ()), ("katixiya", "卡提希娅", ()), ("xiakong", "夏空", ()),
    ("zanni", "赞妮", ()), ("kanteleila", "坎特蕾拉", ()), ("bulante", "布兰特", ()),
    ("feibi", "菲比", ()), ("luokeke", "洛可可", ()), ("keleita", "珂莱塔", ()),
    ("chun", "椿", ()), ("shouanren", "守岸人", ()), ("xiangliyao", "相里要", ()),
    ("zhezhi", "折枝", ()), ("changli", "长离", ()), ("jinxi", "今汐", ()),
    ("yinlin", "吟霖", ()), ("jiyan", "忌炎", ()), ("kakaluo", "卡卡罗", ()),
    ("lingyang", "凌阳", ()), ("jianxin", "鉴心", ()), ("weilinai", "维里奈", ()),
    ("anke", "安可", ()), ("rover-aero", "漂泊者·气动", ("气动漂泊者", "漂泊者气动")), ("buling", "卜灵", ()),
    ("dengdeng", "灯灯", ()), ("youhu", "釉瑚", ()), ("yuanwu", "渊武", ()),
    ("danjin", "丹瑾", ()), ("sanhua", "散华", ()), ("moteifei", "莫特斐", ()),
    ("taoqi", "桃祈", ()), ("qiushui", "秋水", ()), ("baizhi", "白芷", ()),
    ("yangyang", "秧秧", ()), ("chixia", "炽霞", ()),
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sources",
        nargs="*",
        metavar="SOURCE.png",
        help="Four source grids in atlas order. Defaults to work/portrait-sources/grid-01.png through grid-04.png.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT,
        help="Project-relative root that receives data/portraits and data/portrait-atlas.json (default: project root).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Project-relative manifest destination; defaults to OUTPUT_ROOT/data/portrait-atlas.json.",
    )
    arguments = parser.parse_args()
    if arguments.sources and len(arguments.sources) != 4:
        parser.error("exactly four source PNG paths are required when sources are supplied")
    return arguments


def within(base: Path, candidate: Path, label: str) -> Path:
    resolved_base = base.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_base)
    except ValueError as error:
        raise SystemExit(f"error: {label} must be within {resolved_base}: {resolved_candidate}") from error
    return resolved_candidate


def pixel_box(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


def card_box(source_index: int, card_index: int) -> dict[str, int]:
    grid = GRIDS[source_index]
    row, column = divmod(card_index, 3)
    if source_index == 0 and card_index == 0:
        return pixel_box(45, 44, 236, 300)
    return pixel_box(grid["xs"][column], grid["ys"][row], grid["width"], grid["height"])


def face_box(card: dict[str, int]) -> dict[str, int]:
    # This crop starts below the attribute badge, ends above the text panel, and
    # stays left of the lower-right lock marker found in some source cards.
    return pixel_box(card["x"] + 14, card["y"] + 75, 160, 135)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_files(sources: Iterable[Path]) -> tuple[Path, ...]:
    resolved_sources = tuple(source.expanduser().resolve() for source in sources)
    missing = [str(source) for source in resolved_sources if not source.is_file()]
    if missing:
        paths = "\n  ".join(missing)
        raise SystemExit(
            "error: one or more source images are unavailable. Provide four explicit PNG paths, for example:\n"
            "  .venv\\Scripts\\python scripts\\extract_portraits.py first.png second.png third.png fourth.png\n"
            f"Unavailable:\n  {paths}"
        )
    return resolved_sources


def build_atlas(sources: tuple[Path, ...], output_root: Path, manifest_path: Path) -> None:
    portraits_directory = output_root / "data" / "portraits"
    portraits_directory.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    source_metadata: list[dict[str, object]] = []
    for source_index, source_path in enumerate(sources):
        with Image.open(source_path) as source:
            source_metadata.append({
                "source_index": source_index,
                "pixel_size": {"width": source.width, "height": source.height},
                "sha256": sha256(source_path),
            })
            cards_in_source = 11 if source_index == 3 else 15
            source_start = source_index * 15
            for card_index in range(cards_in_source):
                image_id, canonical, aliases = CHARACTERS[source_start + card_index]
                card = card_box(source_index, card_index)
                face = face_box(card)
                right = face["x"] + face["width"]
                bottom = face["y"] + face["height"]
                if face["x"] < 0 or face["y"] < 0 or right > source.width or bottom > source.height:
                    raise ValueError(f"Out-of-bounds face box for {canonical}: {face}")
                crop = source.crop((face["x"], face["y"], right, bottom))
                crop.save(portraits_directory / f"{image_id}.png", format="PNG")
                entries.append({
                    "id": image_id,
                    "canonical": canonical,
                    "aliases": list(aliases),
                    "image": f"data/portraits/{image_id}.png",
                    "source_index": source_index,
                    "card_index": card_index,
                    "card_bbox": card,
                    "face_bbox": face,
                })

    manifest = {
        "schema_version": 1,
        "crop_policy": {
            "method": "lossless source-pixel crop",
            "face_crop_size": {"width": 160, "height": 135},
            "description": "Face/upper-portrait crop excludes the top-left attribute marker, bottom name panel, and lower-right lock marker area.",
        },
        "sources": source_metadata,
        "portraits": entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    arguments = parse_arguments()
    output_root = within(PROJECT_ROOT, PROJECT_ROOT / arguments.output_root, "--output-root")
    manifest_candidate = arguments.manifest or output_root / "data" / "portrait-atlas.json"
    manifest_path = within(output_root, output_root / manifest_candidate if not manifest_candidate.is_absolute() else manifest_candidate, "--manifest")
    sources = validate_source_files(tuple(Path(source) for source in arguments.sources) if arguments.sources else DEFAULT_SOURCES)
    if len(CHARACTERS) != 56:
        raise RuntimeError(f"Expected exactly 56 character records, found {len(CHARACTERS)}")
    build_atlas(sources, output_root, manifest_path)
    print(f"Wrote 56 portraits to {output_root / 'data' / 'portraits'}")
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
