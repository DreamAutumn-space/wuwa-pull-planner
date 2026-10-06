"""Export reviewed source literals independently of raw OCR and optimizer inputs."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export_transcription(payload: dict, review: dict) -> dict:
    if payload['source']['sha256'] != review.get('source_sha256'):
        raise ValueError('Transcription belongs to a different source image.')
    panels = {panel['id']: panel for panel in payload['panels']}
    records = []
    for item in review['panels']:
        panel = panels[item['id']]
        for index, table in enumerate(item.get('tables', [])):
            if table.get('numeric_verification') != 'complete_visual_transcription':
                continue
            headers, rows = table.get('headers', []), table.get('rows', [])
            if not headers or not rows:
                raise ValueError(f"Missing full table: {item['id']} / {index}")
            if any(len(row) != len(headers) for row in rows):
                raise ValueError(f"Column count mismatch: {item['id']} / {index}")
            if any(cell is not None and not isinstance(cell, str) for row in rows for cell in row):
                raise ValueError('Source literals must be strings or null; never inferred numbers.')
            record = deepcopy(table)
            record.update(id=f"{panel['id']}/table-{index+1}", panel_id=panel['id'],
                          section_id=panel['section_id'],
                          characters=[character['name'] for character in panel['characters']],
                          optimizer_eligible=False)
            records.append(record)
    return {'schema_version': 'dps-source-transcription-1', 'source': deepcopy(payload['source']),
            'unit': review.get('unit'), 'unit_note': review.get('unit_note'),
            'status': 'visually_transcribed_source_literals_configuration_mapping_pending',
            'summary': {'table_count': len(records), 'row_count': sum(len(t['rows']) for t in records)},
            'tables': records,
            'calculation_blockers': [
                'Printed cumulative upgrade labels must be mapped to all three character and weapon requirements.',
                'A few upgrades alter the rotation; higher chains cannot automatically use lower-chain DPS.',
                'Effects, single-character DPS and conditional team-DPS columns must remain distinct.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT/'data/dps-recognition-v3.5.2.json')
    parser.add_argument('--review', type=Path, default=ROOT/'data/dps-review-v3.5.2.json')
    parser.add_argument('--output', type=Path, default=ROOT/'data/dps-transcription-v3.5.2.json')
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding='utf-8'))
    review = json.loads(args.review.read_text(encoding='utf-8'))
    result = export_transcription(payload, review)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    args.output.write_text(serialized, encoding='utf-8')
    (ROOT/'outputs/dps-transcription-v3.5.2.json').write_text(serialized, encoding='utf-8')
    print(result['summary'])


if __name__ == '__main__': main()
