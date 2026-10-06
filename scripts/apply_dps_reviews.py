"""Apply explicit source-bound review notes without changing raw OCR evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def merge_reviews(base: dict, update: dict) -> dict:
    """Merge sparse, source-bound observations without erasing earlier checks."""
    if base.get('source_sha256') != update.get('source_sha256'):
        raise ValueError('Cannot merge reviews for different source images.')
    result = deepcopy(base)
    by_id = {item['id']: item for item in result.setdefault('panels', [])}
    for item in update.get('panels', []):
        if item['id'] not in by_id:
            copied = deepcopy(item)
            result['panels'].append(copied)
            by_id[item['id']] = copied
            continue
        previous = by_id[item['id']]
        if len(previous['characters']) != len(item['characters']):
            raise ValueError('Reviewed avatar count differs between observations.')
        previous['characters'] = [new if new is not None else old
            for old, new in zip(previous['characters'], item['characters'])]
        for key, value in item.items():
            if key not in {'id', 'characters'} and (key != 'tables' or value):
                previous[key] = deepcopy(value)
    return result


def apply_reviews(payload: dict, review: dict) -> dict:
    if payload.get('source', {}).get('sha256') != review.get('source_sha256'):
        raise ValueError('Review belongs to a different source image; refusing to apply it.')
    result = deepcopy(payload)
    by_id = {panel['id']: panel for panel in result['panels']}
    seen = set()
    for item in review['panels']:
        if item['id'] in seen:
            raise ValueError(f"Duplicate reviewed panel: {item['id']}")
        seen.add(item['id'])
        panel = by_id.get(item['id'])
        if panel is None:
            raise ValueError(f"Reviewed panel missing: {item['id']}")
        if len(panel['characters']) != len(item['characters']):
            raise ValueError('Reviewed avatar count differs from detected source slots.')
        for character, name in zip(panel['characters'], item['characters']):
            if name is None:
                continue
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Reviewed character names must be nonempty strings or null.')
            character.setdefault('automatic_name', character['name'])
            character.setdefault('automatic_unknown', character['unknown'])
            character['name'] = name
            character['unknown'] = False
            character['identity_verification'] = 'manual_source_and_named_portrait_check'
        panel['manual_review'] = deepcopy(item)
        tables = item.get('tables', [])
        if tables:
            for field in ('difficulty', 'stability'):
                if field in tables[0]:
                    panel.setdefault(f'automatic_{field}', panel.get(field))
                    panel[field] = tables[0][field]
        panel['optimizer_eligible'] = False
    result['manual_review'] = {key: deepcopy(value) for key, value in review.items() if key != 'panels'}
    result['summary']['automatic_unknown_avatar_slots'] = sum(
        character.get('automatic_unknown', character['unknown'])
        for panel in result['panels'] for character in panel['characters']
    )
    result['summary']['unknown_avatar_slots'] = sum(
        character['unknown'] for panel in result['panels'] for character in panel['characters']
    )
    result['summary']['manually_checked_avatar_slots'] = sum(
        'identity_verification' in character for panel in result['panels'] for character in panel['characters']
    )
    result['summary']['table_count'] = sum(len(panel.get('tables', [])) for panel in result['panels'])
    full_tables = [table for panel in review['panels'] for table in panel.get('tables', [])
                   if table.get('numeric_verification') == 'complete_visual_transcription']
    result['summary']['visually_transcribed_table_count'] = len(full_tables)
    result['summary']['visually_transcribed_row_count'] = sum(len(table.get('rows', [])) for table in full_tables)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/dps-recognition-v3.5.2.json')
    parser.add_argument('--review', type=Path, default=ROOT / 'data/dps-review-v3.5.2.json')
    parser.add_argument('--merge-review', action='append', type=Path, default=[],
                        help='Merge sparse browser corrections while keeping full transcription tables.')
    parser.add_argument('--save-review', action='store_true',
                        help='Save the merged source-bound review for subsequent OCR runs.')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/dps-recognition-v3.5.2.json')
    parser.add_argument('--delivery', type=Path, default=ROOT / 'outputs/dps-recognition-v3.5.2.json')
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding='utf-8'))
    review = json.loads(args.review.read_text(encoding='utf-8'))
    for path in args.merge_review:
        review = merge_reviews(review, json.loads(path.read_text(encoding='utf-8')))
    result = apply_reviews(payload, review)
    if args.save_review:
        args.review.write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    for path in (args.output, args.delivery):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding='utf-8')
    print(result['summary'])


if __name__ == '__main__':
    main()
