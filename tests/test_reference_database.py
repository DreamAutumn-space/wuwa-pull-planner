"""Business-rule checks for translating the published table into endpoints."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from build_reference_database import compile_database, parse_label


def load(name):
    return json.loads((ROOT/'data'/name).read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def compiled():
    return compile_database(load('dps-transcription-v3.5.2.json'), load('signature-weapons.json'))[0]


def find(database, table, label):
    return next(r for r in database['records'] if r['source']['table_id'] == table and r['source']['label'] == label)


def test_cumulative_prerequisites_and_unspecified_signature_is_rank_one(compiled):
    record = find(compiled, '3.0_upper-panel-01/table-1', '千专')
    members = {m['character']: m for m in record['members']}
    assert members['绯雪']['chain'] == 6
    assert members['绯雪']['refinement'] == 5
    assert members['千咲']['chain'] == members['守岸人']['chain'] == 2
    assert members['千咲']['weapon'] == '昙切'
    assert members['千咲']['refinement'] == 1
    assert members['守岸人']['weapon'] == '表内常驻·守岸人'
    final = find(compiled, '3.0_upper-panel-01/table-1', '6守')
    assert all(member['chain'] == 6 for member in final['members'])
    assert final['members'][2]['weapon'] == '星序协响'
    assert final['members'][2]['refinement'] == 1


def test_zero_gain_prerequisite_is_not_dropped(compiled):
    low = find(compiled, '3.0_upper-panel-13/table-1', '4链')
    passing = find(compiled, '3.0_upper-panel-13/table-1', '5链')
    high = find(compiled, '3.0_upper-panel-13/table-1', '6链')
    assert low['rotations'][0]['dps'] == passing['rotations'][0]['dps']
    assert passing['members'][0]['chain'] == 5
    assert high['members'][0]['chain'] == 6


def test_known_chain_drop_does_not_promote_higher_chain_to_lower_axis(compiled):
    low = find(compiled, '3.0_upper-panel-04/table-2', '6千')
    high = find(compiled, '3.0_upper-panel-04/table-2', '6琳')
    assert low['rotations'][0]['dps'] == 41.95
    assert high['rotations'][0]['dps'] == 41.90
    assert next(m for m in low['members'] if m['character'] == '琳奈')['max_chain'] == 5
    assert next(m for m in high['members'] if m['character'] == '琳奈')['chain'] == 6


def test_conditional_columns_remain_source_values_not_optimistic_maximum(compiled):
    record = find(compiled, '3.0_upper-panel-13/table-1', '0链')
    assert record['rotations'][0]['dps'] == 8.41
    assert record['source']['team_column'] == '全队0层'
    assert record['source']['row'][3:] == ['8.92', '9.67']


def test_confirmed_aero_rover_roster_is_a_non_gacha_baseline(compiled):
    record = find(compiled, '2.0_mid-panel-07/table-1', '2链')
    members = {member['character']: member for member in record['members']}
    assert set(members) == {'卡提希娅', '夏空', '漂泊者·气动'}
    assert members['漂泊者·气动'] == {
        'character': '漂泊者·气动',
        'chain': 6,
        'max_chain': 6,
        'weapon': '表内常驻·漂泊者·气动',
        'refinement': 1,
    }
    assert {'漂泊者·导电', '漂泊者·衍射', '漂泊者·气动'} <= set(compiled['metadata']['non_gacha_characters'])


def test_missing_values_and_unrecognized_labels_are_not_guessed(compiled):
    assert not any(r['source']['table_id'] == '2.0_lower-panel-05/table-1' for r in compiled['records'])
    assert not any(r['source']['table_id'] == '2.0_mid-panel-04/table-1' and r['source']['label'] == '1链' for r in compiled['records'])
    source = load('dps-transcription-v3.5.2.json')
    table = deepcopy(source['tables'][0])
    table['rows'][1][0] = '未知提升'
    source['tables'] = [table]
    result, _ = compile_database(source, load('signature-weapons.json'))
    assert result['records'] == []
    assert result['metadata']['excluded_tables']


def test_compound_chain_refinement_and_multiple_roles():
    names = ['绯雪', '琳奈', '穗穗']
    operations, _, _ = parse_label('6琳3穗', names, names[0], 1)
    assert operations == [{'character': '琳奈', 'chain': 6}, {'character': '穗穗', 'chain': 3}]
    operations, _, _ = parse_label('65琳', names, names[0], 1)
    assert operations == [{'character': '琳奈', 'chain': 6, 'signature_refinement': 5}]


def test_generated_database_matches_checked_in_artifact(compiled):
    assert compiled == load('reference-dps-v3.5.2.json')
    assert compiled['metadata']['mapped_table_count'] == 64
    assert compiled['metadata']['mapped_row_count'] == 811
    ids = [record['id'] for record in compiled['records']]
    assert len(ids) == len(set(ids))
