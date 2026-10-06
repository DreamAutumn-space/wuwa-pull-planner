"""Pinned-source human corrections must survive future OCR runs."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT/'data'/name).read_text(encoding='utf-8'))


def test_all_source_roles_reviewed_and_similar_portraits_are_not_conflated():
    doc = load('dps-recognition-v3.5.2.json')
    panels = {p['id']: p for p in doc['panels']}
    assert doc['source']['sha256'] == hashlib.sha256((ROOT/'data/public/reference-dps.jpg').read_bytes()).hexdigest()
    assert doc['summary']['manually_checked_avatar_slots'] == 150
    assert doc['summary']['unknown_avatar_slots'] == 0
    for pid in ['3.0_lower-panel-01','3.0_lower-panel-02','3.0_lower-panel-03',
                '3.0_upper-panel-01','3.0_upper-panel-02','3.0_upper-panel-03','3.0_upper-panel-04']:
        assert panels[pid]['characters'][0]['name'] == '绯雪'
    assert panels['3.0_lower-panel-03']['characters'][1]['name'] == '洛瑟菈'
    assert panels['2.0_mid-panel-03']['characters'][1]['name'] == '坎特蕾拉'
    assert panels['2.0_lower-panel-02']['characters'][0]['name'] == '嘉贝莉娜'
    assert panels['1.0-panel-03']['characters'][1]['name'] == '吟霖'
    assert panels['2.0_mid-panel-07']['characters'][2]['name'] == '漂泊者·气动'
    assert panels['2.0_mid-panel-09']['characters'][2]['name'] == '漂泊者·衍射'


def test_all_tables_preserve_verified_literals_and_special_columns():
    doc = load('dps-transcription-v3.5.2.json')
    tables = {t['id']: t for t in doc['tables']}
    assert doc['summary'] == {'table_count': 65, 'row_count': 824}
    assert all(not table['optimizer_eligible'] for table in tables.values())
    assert all(len(row)==len(t['headers']) for t in tables.values() for row in t['rows'])
    table = tables['3.0_upper-panel-04/table-2']
    assert table['rows'][-2][1] == '41.95'
    assert table['rows'][-1][1] == '41.90'
    table = tables['3.0_upper-panel-13/table-1']
    assert table['headers'] == ['链子','全队0层','露单人','全队1层','全队2层']
    assert table['rows'][-2][2] == table['rows'][-1][2] == '0.00'
    assert tables['3.0_lower-panel-04/table-1']['rows'][7][2] == '39.95'
    assert tables['2.0_mid-panel-01/table-1']['rows'][-1][0] == '6守'
