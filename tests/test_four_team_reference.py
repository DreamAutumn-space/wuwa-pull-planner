"""Four disjoint reference teams may share Suisui within her two-team limit."""
import json
from pathlib import Path

from wuwa_optimizer import optimize
from wuwa_optimizer.reference_adapter import prepare_reference_inputs

ROOT = Path(__file__).resolve().parents[1]


def test_four_team_current_includes_aero_rover_and_repeated_suisui():
    database = json.loads((ROOT / 'data/reference-dps-v3.5.2.json').read_text(encoding='utf-8'))
    chosen_ids = {
        '3.0_upper-panel-10/table-1/row-04',
        '3.0_lower-panel-04/table-2/row-02',
        '2.0_mid-panel-07/table-1/row-01',
        '2.0_lower-panel-07/table-1/row-01',
    }
    # Generate a minimal inventory from public reference requirements, rather
    # than retaining an uploaded player's full account in a test fixture.
    selected = [row for row in database['records'] if row['id'] in chosen_ids]
    assert len(selected) == 4
    chains = {}
    weapons = {}
    for row in selected:
        for member in row['members']:
            chains[member['character']] = max(chains.get(member['character'], 0), member['chain'])
            if not member['weapon'].startswith('表内常驻·'):
                weapons[member['weapon']] = max(weapons.get(member['weapon'], 1), member['refinement'])
    account = {
        'characters': [{'character': name, 'chain': chain} for name, chain in chains.items()],
        'weapons': [{'id': f'signature-{index}', 'weapon': weapon, 'refinement': rank}
                    for index, (weapon, rank) in enumerate(weapons.items())],
    }
    account, prepared = prepare_reference_inputs(account, database, assume_common_weapons=True)
    result = optimize(account, prepared, {
        'mode': 'four_teams', 'budget': 0, 'max_difficulty': '中', 'search_limit': 250000,
    })
    assert result['current']['feasible'] is True
    assert result['current']['total_dps'] == 46.54
    assert result['best']['cost'] == 0
    expected = {
        frozenset({'爱弥斯', '达妮娅', '穗穗'}),
        frozenset({'秧秧·玄翎', '千咲', '穗穗'}),
        frozenset({'卡提希娅', '夏空', '漂泊者·气动'}),
        frozenset({'尤诺', '琳奈', '守岸人'}),
    }
    teams = result['current']['teams']
    assert {frozenset(m['character'] for m in team['members']) for team in teams} == expected
    # Repeating a healer still assigns separate physical weapons.
    suisui_weapons = [assignment['instance_id'] for team in teams
                     for assignment in team['weapon_assignment'] if assignment['character'] == '穗穗']
    assert len(suisui_weapons) == len(set(suisui_weapons)) == 2
